# ADR 0022 — Profili di conversazione (multi-comportamento per merchant)

Data: 2026-07-26
Stato: accettato (design congelato; implementazione Step 4-6 da fare)

## Contesto

Caso d'uso del cliente: un merchant deve poter avere **più configurazioni per
obiettivi diversi**, con **una di default**, e la possibilità che
un'**automazione carichi** una configurazione diversa su una conversazione.
Esempio: un parrucchiere ha di default il profilo **"Reception"** (assiste un
utente base, capisce la richiesta); ma esiste anche **"Consulenza telefonica"**,
caricato quando parte una determinata automazione, dove il bot sa di doversi
comportare in un certo modo.

Stato del codice oggi (audit 2026-07-24, verificato):

- Un merchant ha **una sola config effettiva**. `bot_configs.merchant_id` è
  `unique=True` (vincolo hard a DB) e `ConfigResolver.resolve(key, *, merchant_id)`
  è indicizzato **solo** su `merchant_id` (cache key `cfg:{merchant_id}:{key}`).
- La persona/comportamento è risolta **per-merchant** in due punti gemelli:
  inbound (`build_cascade_system_prompt` + `resolve_playbook_runtime` in
  `conversation_service`) e proattivo (`_build_ai_reply_deps` in
  `workers/automation/engine.py`). Nessuno dei due sa "quale automazione/profilo"
  ha avviato la conversazione.
- L'unico override per-conversazione esistente è l'**A/B variant** (`variant_id`),
  ma fa **swap totale** del solo system prompt via `PromptManager`, bypassando
  cascade/sentiment/playbook; è **stocastico** (un solo esperimento "running" per
  merchant) e senza identità (niente `name`/`is_default`).
- Il **playbook** (`conversation.playbook.mode/goal/directives/actions`) è già
  concettualmente un "profilo comportamentale", ma risolto per-leaf per un solo
  merchant.
- Le automazioni sono **trigger-emitter stateless** (ADR 0015): nessun nodo scrive
  un marcatore di config/persona sulla conversazione. `conversation.meta` (JSONB)
  esiste ma non ospita alcun profilo.

## Decisione

**Introdurre un concetto nuovo e leggero `profilo` (NON generalizzare l'A/B),
riusandone il *pattern* di threading per-conversazione. Un profilo è un DELTA
sulla persona base del merchant, selezionato per-conversazione, caricabile da
un'automazione o da una pipeline.**

### Decisioni di prodotto congelate (con l'utente, 2026-07-24)

1. **DELTA, non swap.** Il profilo sovrascrive solo alcuni knob sopra la persona
   base del merchant (che resta condivisa: business info, KB, booking). Non
   riscrive il prompt da zero.
2. **Scope V1 = solo comportamento.** Il profilo modula `conversation.playbook.*`
   (goal, directives, `actions.enabled`, `mode` per spegnere la FSM vendita su un
   profilo non-sales) + `bot.system_prompt_additions` + i knob `bot.*` di
   tono/stile. **Non** tocca booking, scoring, RAG, `model_override` (rinviato a V2).
3. **Durata = fino a fine episodio.** Il profilo caricato dura per la chat attiva;
   si resetta al profilo `is_default` a fine episodio (idle/chiusura) o se un
   altro trigger lo cambia. Coerente col motore stateless.
4. **Ownership = agency + merchant.** L'agency definisce profili-libreria
   riusabili (come `BotTemplate`); il merchant li adotta e personalizza.

### Bivi minori (default, vetabili)

- **Precedenza** (pipeline vs automazione sulla stessa chat): *ultimo-scrittore-
  vince*. Coerente con "fino a fine episodio"; siccome l'automazione parte dopo la
  creazione da pipeline, vince l'automazione (= il caso "Consulenza").
- **Convivenza con A/B**: ortogonali. Se una chat ha un variant A/B attivo che fa
  swap del *body*, il profilo modula **solo** playbook/azioni/FSM (non il body
  swappato). Senza variant, il profilo agisce sulla cascade normalmente.
- **Carrier**: `conversation.meta['active_profile_id']` in V1 (**nessuna
  migrazione**); colonna dedicata analoga a `variant_id` in V2 se serve indice.

### Disegno tecnico

1. **Tabella `bot_profiles`** (`id, tenant_id, merchant_id, key, name,
   description, is_default, overrides JSONB` con la **stessa shape** validata da
   `BotConfigSchema`, opzionale `model_override` per V2) + **RLS** su
   `tenant_id/merchant_id` + migrazione + estensione `test_isolation`. Un profilo
   = override-bag DELTA che vince sul base config del merchant.
2. **Livello-0 per-conversazione nel resolver**: `resolve(key, *, merchant_id,
   profile_overrides=None)`. Con `profile_overrides=None` (default) il
   comportamento è **identico a oggi** → i ~40 call-site restano invariati. Cache
   key namespaced `cfg:{merchant_id}:{profile_key}:{key}`. `build_cascade_system_prompt`
   e `resolve_playbook_runtime` guadagnano il param opzionale `profile_overrides`.
3. **Lettura (due call-site, entrambi obbligatori)**: `active_profile_id` in
   `_ReplyContext`, catturato ai due build-site dove `conv` è in scope, e
   consumato **a monte** di `_resolve_system_prompt`/`resolve_playbook_runtime`
   nell'inbound **E** in `_build_ai_reply_deps` nel proattivo. Saltare il secondo
   fa sì che l'ai_reply dell'automazione ignori il profilo che l'automazione
   stessa ha appena caricato — incoerenza proprio nel caso d'uso.
4. **Scrittura da automazione**: nuovo nodo azione `set_conversation_profile`
   (`ACTION_TYPES` + validazione + ramo in `_do_action`, modellato su
   `set_lead_field`) che fa `jsonb_set` su `conversations.meta['active_profile_id']`
   e ritorna `False` (nessun invio). Il turno inbound successivo legge il profilo.
5. **Scrittura da pipeline**: `_handle_crm_create` ha già `pipeline_id`/`stage_id`
   in scope alla creazione conversazione → mappare `pipeline_id → profile_id`.
6. **Default merchant**: `ConversationRepository.create` guadagna
   `active_profile_id` (default = profilo `is_default` del merchant), passato dai
   tre call-site di creazione.

Il layer di assemblaggio prompt (`orchestrator._build_messages`) è già
profile-agnostic e **non si tocca**: il gap è tutto nel layer di *risoluzione*.

## Perché non generalizzare l'A/B

I profili devono essere **deterministici e sempre-selezionabili** (l'A/B è
stocastico e vincolato a un esperimento "running"), avere **identità**
(`name`/`is_default`), essere un **DELTA** (l'A/B fa swap totale bypassando la
cascade), e **coesistere** (più profili attivi su conversazioni diverse). Sono
**ortogonali** all'A/B e devono comporre (un profilo può ospitare un esperimento
A/B sul suo prompt). Riusare `variant_id` occuperebbe lo slot dell'A/B e perderebbe
la persona base.

## Conseguenze

- **Doppio call-site**: il rischio n.1. Aggiornare l'inbound ma non
  `_build_ai_reply_deps` rompe esattamente il caso "Consulenza caricata da
  automazione". Va coperto da test su entrambi i path.
- **Cache Redis**: la key va namespaced col profilo, e l'invalidazione
  (`cfg:{merchant_id}:*`) verificata, altrimenti collisioni tra profili.
- **Reset a fine episodio** da agganciare alla stessa nozione di episodio del
  motore stateless (idle/chiusura), non a uno stato di run.
- **Nuova tabella = nuove policy RLS** obbligatorie + estensione degli isolation
  test con due tenant.
- **FSM vendita hardcoded** (`state_machine.py`): un profilo non-sales
  ("Consulenza") in V1 usa `mode='off'` per spegnere gli hint di stato; una FSM
  alternativa per profilo è V2.
- **Collegamento con ADR 0021**: per "quanti booking dal profilo X" serve
  thread-are `active_profile_id`/`automation_id` nelle emit analytics + le
  dimensioni V2 dell'ADR 0021 (GIN su properties). I due assi si saldano lì.
- **Nessuna migrazione dati** sullo storico: i profili valgono da qui in avanti.
