# ADR 0013 — Loop tool-use dell'agente + default "umani" e fail-safe

Status: Accepted (2026-06-23)

## Context

L'audit comparativo con **Amalia** (`docs/audit-amalia-vs-reloop-2026-06-23.md`,
piattaforma di riferimento che funziona bene in produzione) ha isolato il gap più
impattante tra le due piattaforme: **il paradigma dell'agente**.

- **Amalia** esegue un vero loop tool-use nativo: il modello chiama un tool, *vede
  il risultato reale* (disponibilità calendario, ordine, inventario) reiniettato
  come osservazione, ragiona e adatta la risposta **nello stesso turno**.
- **ReloopxWave** usava un singolo passaggio "structured-JSON actions": una sola
  chiamata LLM emetteva `{reply_text, actions[]}`, le azioni giravano **dopo** la
  reply come effetti collaterali best-effort, e il modello **non vedeva mai
  l'esito**. Conseguenza: il bot poteva dire "ti ho prenotato alle 15" mentre
  l'handler `book_slot`, eseguito subito dopo, scopriva lo slot occupato e inviava
  un secondo messaggio contraddittorio ("quello slot non è più disponibile…").

Inoltre l'audit ha trovato tre rischi minori ma reali: nessun fail-safe (su un
errore LLM duro il cliente restava in **silenzio**), nessun controllo di
**staleness** sull'inbound (dopo un downtime il bot rispondeva a un backlog vecchio
fuori contesto), e i knob di consegna "umana" (ADR 0008) erano **tutti a default
no-op** → out-of-the-box il bot sembrava un robot.

## Decision

### 1. Loop tool-use selettivo nell'orchestrator (Amalia-style)
`ConversationOrchestrator.run(...)` accetta `tool_executor` + `max_iterations` e
diventa un loop iterativo (default `max_iterations=1` = comportamento single-shot
invariato). Due nuove `ActionKind` **di sola lettura** — `check_availability`
(disponibilità reale su GHL) e `lookup_appointment` (prossimo appuntamento dal
mirror locale) — vengono eseguite **mid-turn**: l'esito viene reiniettato come
osservazione (`role=user`, "RISULTATO STRUMENTI…") e il modello produce la risposta
definitiva veritiera. Le read-action sono **rimosse** dalle azioni restituite (non
raggiungono mai il dispatcher post-turno). L'executor (`GhlReadToolExecutor` in
`ai_core/actions/read_tools.py`) è iniettato in `ConversationService` dal runtime,
così l'orchestrator resta **IO-free**. Gate config: `agent.tool_use_enabled`
(default on) + `agent.max_tool_iterations` (default 3).

Regola anti-falsa-conferma nello schema prompt: per `book_slot/reschedule_slot/
cancel_slot/propose_slots` il `reply_text` deve essere una frase di attesa
("procedo e ti confermo"), perché la conferma reale con l'esito la invia l'handler
**dopo**.

### 2. Fail-safe "risposta sempre" (QW1)
`_generate_and_deliver` cattura ogni eccezione dell'orchestrator: invia un messaggio
di cortesia (`escalation.handoff_message` o un default) e inietta `escalate_human`
così il thread passa a un operatore. Su errore non si sopprime mai la reply, anche
con silent-handoff configurato. Porting di `handle_ai_conversation_safe` di Amalia.

### 3. Staleness check inbound (QW2)
`schedule.inbound_staleness_min` (default 10, 0 = off). Il timestamp Meta del
messaggio (`messages[].timestamp`) viaggia dal webhook → worker → `handle_inbound_persist`;
se l'inbound è più vecchio della soglia l'auto-reply è soppressa (il messaggio è
**comunque persistito**, reason `stale`).

### 4. Default delivery "human-feel" (QW3)
I `delivery.*` (ADR 0008) passano da no-op a default umani: debounce 8s, typing
indicator on, pausa 1–6s con jitter, fino a 2 bolle. I merchant possono riazzerarli
via cascade. I fallback **a livello di codice** (`_resolve_*(default=…)`) restano
no-op: una *failure* di risoluzione degrada all'invio istantaneo (sicuro) e i unit
test, che bypassano il resolver, non rallentano.

### 5. Throttler outbound 360dialog (QW5)
`D360WhatsAppClient` ora limita la frequenza per canale (in-process, keyed su
`phone_number_id`, default 8 msg/s, `ratelimit.py`) e rispetta `Retry-After` sui
429, con retry su 429/5xx e fail-fast sui 4xx. Protegge la quality rating Meta su
burst (multi-bolla, campagne).

## Rejected alternatives

- **Riscrivere l'orchestrator a tool-calling nativo OpenAI** (paradigma identico ad
  Amalia/Anthropic). Avrebbe stravolto lo structured-JSON su cui poggiano scoring
  deterministico, varianti A/B (`PromptManager`), analytics `actions`, playground
  parity e ~380 test. Il loop selettivo ottiene il beneficio chiave (risposte
  grounded sui dati reali) preservando le parti sane. Si può graduare a tool-calling
  nativo in V2 se serve.
- **Eseguire `book_slot` dentro il loop e far comporre al modello la conferma
  finale.** Più invasivo (gli handler già inviano la propria conferma veritiera);
  `check_availability` + la regola anti-falsa-conferma risolvono il 90% del problema
  ("non promettere ciò che non hai verificato") con un decimo del rischio.
- **Cambiare i default delivery anche nei fallback di codice.** Avrebbe introdotto
  `asyncio.sleep` reali nei unit test (resolver che fallisce → default umano).
  Tenuti no-op: SYSTEM_DEFAULTS guida la produzione, il fallback-codice guida il
  degrado/test.
- **Rate limiter Redis distribuito** (raccomandato dall'audit). In-process copre il
  worker ARQ consolidato odierno; il path di upgrade a token-bucket Redis è annotato
  in `ratelimit.py` per il multi-replica.

## Consequences

- Default: **loop tool-use on** (fino a 3 chiamate LLM/turno quando servono dati
  reali), **delivery human-feel on**, **staleness on (10 min)**, **fail-safe sempre**.
- Nuove chiavi config: `agent.tool_use_enabled`, `agent.max_tool_iterations`,
  `schedule.inbound_staleness_min` + nuovi default `delivery.*`. `generated.ts`
  rigenerato (drift OpenAPI risolto).
- Sezioni UI operative (no_answer/reactivation/scoring/booking) **sbloccate** nel
  pannello config merchant (QW4); restano nascoste solo `business` (spostata),
  `rag` e `pipeline` (tecniche).
- Copertura: nuovi unit test per il loop (`test_orchestrator_tool_loop.py`), il
  throttler/Retry-After (`test_d360_ratelimit.py`), il fail-safe e i default
  human-feel; suite a **384 verde**. Il fallback Anthropic ora riceve anche
  `response_format=json_object` (le azioni sopravvivono al failover).
- Limite noto: `check_availability` confronta lo slot richiesto con quelli liberi
  alla precisione del minuto; il booking effettivo resta autorità dell'handler
  `book_slot` (che ri-verifica e propone alternative se serve).
