# ADR 0009 — Playground come preview fedele del flusso WhatsApp reale

Status: Accepted (2026-06-17)

## Context

Il playground (UC-08) era nato come un *banco di prova di prompt*: la colonna di
sinistra esponeva tre leve modificabili dal merchant — una **textarea per il
system prompt**, un **variant id** libero e un toggle **"usa knowledge base"** —
inviate a ogni turno a `POST /playground/turn`.

Questo tradiva l'aspettativa dell'utente: *"il playground deve semplicemente
provare cosa risponderebbe il sistema"*. In pratica rompeva la parità con il
turno WhatsApp reale in più punti:

- **Prompt diverso.** Il `system_prompt` arrivava dal frontend e veniva usato
  **verbatim** (`playground.py`), saltando `_cascade_system_prompt` — quindi
  niente business profile, niente persona strutturata (formality/verbosity/
  emoji/greeting/signature/do-dont/examples, ADR 0008), niente store policy,
  niente sentiment adaptation. Il default era addirittura un prompt generico
  hardcoded nel componente React (`DEFAULT_SYSTEM_PROMPT`).
- **Routing modello falsato.** `lead_score=0` e `hot_threshold=80` erano
  hardcoded: il `hot_threshold` non veniva risolto dalla cascade.
- **Override del chiamante.** `use_kb` e `variant_id` permettevano di deviare
  dal comportamento di produzione.

Router, temperatura e selezione modello erano invece **già** identici al reale
(`router.py` + `llm.py`).

## Decision

Il playground diventa una **preview senza parametri**: il frontend invia solo la
conversazione (`history` + `user_message`); prompt e impostazioni sono risolti
**server-side**, identici a un turno WhatsApp reale.

### 1. Builder del prompt condiviso
Il corpo di `_cascade_system_prompt`/`_store_policy_lines` è estratto in funzioni
**module-level** in `conversation_service.py`
(`build_cascade_system_prompt`, `build_store_policy_lines`); i metodi di
`ConversationService` restano come thin wrapper (così il flusso reale e i test
che patchano `cs.ConfigResolver` non cambiano). `PlaygroundRunner` chiama la
stessa funzione canonica → **stesso identico prompt**.

### 2. Niente override nel contratto
`PlaygroundTurnIn` e `PlaygroundRequest` perdono `system_prompt`, `variant_id`,
`use_kb`. La RAG è sempre attiva se è configurato un embedder; `hot_threshold` è
risolto da `ConfigKey.SCORING_HOT_THRESHOLD` come nel reale.

### 3. Semantica "primo contatto"
Senza una conversazione vera non esiste un lead né un'assegnazione A/B: il
playground gira come **braccio di controllo** (`variant_id=None`) con
`lead_score=0` e `prior_sentiment=None` — esattamente la condizione di un primo
messaggio da un numero WhatsApp sconosciuto.

### 4. UI a colonna singola
Il pannello "Parametri" sparisce. Resta la chat e, sotto, una card **read-only**
con i dettagli dell'ultima risposta (modello, token, latenza, azioni, chunk KB)
come strumento di debug, non come controllo.

## Rejected alternatives

- **Selettore A/B nel playground.** Rimosso `variant_id`: provare un braccio
  specifico è una funzione della pagina A/B testing, non della "preview del
  default". Il playground mostra il comportamento di produzione di base.
- **Estrarre il builder in un modulo nuovo.** I test patchano
  `conversation_service.ConfigResolver` e chiamano `svc._cascade_system_prompt`:
  tenere la funzione nello stesso modulo evita di rompere il monkeypatch e
  mantiene zero churn sul path reale.
- **Simulare un lead "caldo"** (lead_score configurabile). Sarebbe un override
  travestito: rompe la parità "primo contatto". Eventuale feature separata.

## Consequences

- Parità completa su prompt, config-cascade, RAG, selezione modello e
  temperatura. L'unica escalation che resta inattiva è `hot_lead` (manca un lead
  reale) — atteso e documentato; `long_context`, `critical_objection` e
  `many_turns` funzionano identici al reale.
- Invariante UC-08 preservata: nessun invio WhatsApp, nessuna persistenza,
  nessun dispatch di azioni (le azioni tornano solo come metadati).
- `generated.ts` rigenerato (rimossi i tre campi da `PlaygroundTurnIn`). 2 nuovi
  unit test (`test_uc08_playground.py`) pinnano il contratto di parità.
