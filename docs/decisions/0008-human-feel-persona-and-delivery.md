# ADR 0008 — Persona strutturata + consegna "umana" del bot

Status: Accepted (2026-06-16)

## Context

La personalizzazione della voce del bot era debole e confondeva due piani distinti:

- **Contenuto (cosa scrive).** L'unica leva di tono era `bot.tone`, una **stringa libera** interpolata pari‑pari nel system prompt (`conversation_service.py`, `f"...mantieni un tono {tone}..."`), più una textarea da 4000 caratteri (`bot.system_prompt_additions`). Imprevedibile e tutta a carico del merchant. Inoltre il **sentiment** veniva già calcolato a ogni turno (gpt‑5‑nano, `sentiment.py`) ma usato solo per scoring/analytics: **mai** per modulare la risposta.
- **Consegna (come arriva).** La risposta auto‑reply partiva **sincrona e istantanea** dentro il job inbound (`self._sender.send(...)`), senza indicatore "sta scrivendo…", come un unico muro di testo, e **senza debounce**: 3 messaggi ravvicinati del cliente generavano 3 run e 3 risposte — anche un bug, perché il bot rispondeva al primo messaggio prima di vedere gli altri.

## Decision

Trattare i due piani separatamente, tutto **config‑cascade** e disattivabile.

### 1. Persona strutturata (contenuto)
Nuovi knob guidati in `BotSurfaceConfig` che mappano a **frammenti italiani deterministici** nel system prompt (`_FORMALITY_FRAGMENTS`/`_VERBOSITY_FRAGMENTS`/`_EMOJI_FRAGMENTS`):
`bot.formality` (tu/Lei/auto), `bot.verbosity`, `bot.emoji_policy`, `bot.greeting_style`, `bot.signature`, `bot.do_phrases`, `bot.dont_phrases`, `bot.examples` (few‑shot di stile). La textarea libera resta come escape‑hatch "Avanzate" e vince sempre (ultima nel prompt).

### 2. Sentiment del **turno precedente** (contenuto)
L'adattamento empatico usa `lead.sentiment` già su file (caricato in fase 1), **non** una nuova chiamata sentiment prima della risposta. Gate: `bot.sentiment_adaptation_enabled` (default on). neutral/None non iniettano nulla.

### 3. Consegna umana (delivery)
Nuovo `DeliveryConfig` (`delivery.*`) con default **no‑op** (comportamento odierno):
debounce dei messaggi in arrivo, ritardo "digitazione" calcolato dalla lunghezza, indicatore "sta scrivendo…", split in più bolle. Logica pura in `ai_core/delivery.py` (`compute_typing_delay_s`, `split_into_bubbles`, `debounce_decision`).

`handle_inbound` è spezzato in `handle_inbound_persist` (fase 1, **sempre sincrona** → durabilità) + `generate_and_send_reply` (fase 2/3, riusabile). Il worker bufferizza gli inbound in Redis e schedula un **flush ARQ per‑peer** (`wa:flush:{merchant}:{phone}`) che si **auto‑rischedula** finché il cliente non tace per `window`, poi drena il buffer e genera **una** risposta.

L'indicatore typing usa la chiamata Cloud API che 360dialog fa da proxy: `POST /messages` con `status:"read"` + `typing_indicator:{type:"text"}` (auto‑dismiss ~25s), richiede il wamid inbound.

## Rejected alternatives

- **Spostare la chiamata sentiment prima di `orchestrator.run`.** Aggiungerebbe ~300‑800ms (gpt‑5‑nano) sul path critico di *ogni* risposta. Il sentiment del turno precedente è quasi sempre accurato in un thread multi‑turno e costa zero.
- **Far ritornare al modello un array di bolle** (cambiare lo schema JSON dell'orchestrator). Invasivo (tocca ogni prompt, il parser strutturato, le varianti A/B) e fragile. Split deterministico in fase di invio: isolato, puro, model‑agnostico, una sola Message row (history pulita, `message.replied` unico).
- **Spingere il flush con `_defer_by` a ogni messaggio** affidandosi all'abort del job pendente. `Job.abort` di ARQ è version‑sensitive sui job deferred. Lo schema scelto — `_job_id` stabile + epoca di scadenza in Redis + flush che si auto‑rischedula — è deterministico e retry‑safe.
- **Bufferizzare anche la persistenza dell'inbound.** La fase 1 resta sincrona e idempotente su `wa_message_id`: il messaggio del cliente non si perde mai, anche se il flush ritarda o il worker muore.
- **Sostituire del tutto `bot.tone`.** Tenuto come fallback quando `formality="auto"`, così i merchant che l'hanno personalizzato mantengono il comportamento attuale verbatim (zero migrazione: il bag è JSONB, le chiavi non settate risolvono ai system default).

## Consequences

- Default: **persona on** (arricchimento mite del prompt per chi ha già configurato qualcosa; un merchant completamente vuoto resta su `DEFAULT_SYSTEM_PROMPT`), **delivery off** (opt‑in per merchant). 
- Le **varianti A/B** (prompt autorati via `PromptManager`) bypassano persona+sentiment by‑design (integrità esperimenti).
- Multi‑bolla v1: lo stato dei tick di consegna è mappato solo sulla prima bolla (limite noto, accettabile).
- Copertura: 44 nuovi unit test (frammenti persona, sentiment, delay/splitter puri, supersede del debounce con FakeRedis, payload typing). `generated.ts` rigenerato per i nuovi campi.
