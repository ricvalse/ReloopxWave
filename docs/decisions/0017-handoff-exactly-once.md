# ADR 0017 — Handoff exactly-once: claim atomico + un flush job per inbound

Data: 2026-07-06
Stato: accettato

## Contesto

Incidente in produzione: un cliente ha inviato 10 foto di fila su WhatsApp e il
bot ha risposto a **ogni** foto con il messaggio di handoff di default. Il
requisito è l'opposto: il messaggio di handoff parte **una volta sola**, poi il
thread è dell'operatore e il bot tace.

L'analisi ha trovato tre buchi indipendenti, ciascuno sufficiente a produrre il
sintomo (o il suo speculare, il bot muto):

1. **Race di concorrenza.** Il takeover (`auto_reply=false`) veniva applicato
   da `EscalateHumanHandler` **dopo** l'invio della risposta. Un burst di
   inbound (album di foto) esplode in N job ARQ concorrenti (`max_jobs`
   default 10): tutti superano il gate `conv.auto_reply` prima che il primo
   flip committi → N turni LLM → N messaggi di handoff, N eventi
   `conversation.escalated`, N notifiche operatore.
2. **`escalation.enabled=false` incoerente.** La reply-policy sostituiva
   comunque `reply_text` con il messaggio di handoff, ma l'handler saltava il
   takeover → il cliente riceveva "ti passo un operatore" **a ogni messaggio**,
   per sempre, e nessun operatore arrivava.
3. **Debounce rotto con arq 0.28.** `enqueue_job` rifiuta (ritorna `None`,
   silenziosamente) un `_job_id` il cui job key **o result key** esiste ancora
   (`keep_result` default 1h; `WorkerSettings` non lo azzera). Con l'id
   stabile `wa:flush:{merchant}:{phone}`: (a) il self-reschedule dall'interno
   del flush in esecuzione falliva sempre (il job key esiste durante
   l'esecuzione), (b) dopo un flush completato ogni nuovo flush per quel
   contatto era rifiutato per un'ora → messaggi bufferizzati mai risposti. In
   pratica il debounce funzionava solo per il primo burst per contatto/ora —
   il che spiega perché in produzione veniva disattivato (window=0),
   riesponendo il sistema alla race del punto 1.

## Decisione

**1. Il takeover si vince con un claim atomico, prima dell'invio.**
`ConversationRepository.claim_handoff()` è la variante condizionale di
`mark_escalated`: stesso SET (auto_reply, handoff_*, meta legacy) ma con
`WHERE auto_reply = true … RETURNING id` → ritorna `bool` "ho vinto io". Il
row lock di Postgres serializza i turni concorrenti; dopo il commit del
vincitore la WHERE non matcha più. Il claim committa con la sessione di fase 2
(persist), **prima** dell'invio sul filo in fase 3.

Nella reply-policy di `_generate_and_deliver`:

- azione `escalate_human` presente e claim **vinto** → comportamento di prima
  (silent-handoff oppure `handoff_message`; su `llm_failed` mai silenziato);
- claim **perso** → `suppress_reply=true` e azione `escalate_human` rimossa:
  niente messaggio, niente riga assistant, niente ri-dispatch (quindi niente
  seconda notifica operatore / evento analytics);
- `escalation.enabled=false` (e non `llm_failed`) → azione rimossa e **esce la
  risposta del LLM**, non il messaggio di handoff: il thread resta al bot per
  scelta esplicita dell'agency, promettere un operatore sarebbe una bugia
  ripetuta a ogni turno.

Il percorso persist per i media pesanti (`force_handoff_reason`,
video/documento) applica lo stesso principio: stamp + evento solo se l'handoff
non è già pendente.

**2. Un flush job per inbound, mai id riusati.** Il debounce non usa più un
job id stabile: ogni inbound accoda il proprio flush differito con id
`wa:flush:{m}:{phone}:{due_ms}`. Il job dell'**ultimo** messaggio del burst
parte dopo il quiet period e drena tutto (drain `LRANGE`+`DEL` in MULTI,
atomico → job precoci/duplicati trovano la scadenza nel futuro o il buffer
vuoto e sono no-op). Il ramo RescheduleBy resta solo come assicurazione contro
clock skew tra istanze worker, con suffisso `uuid4` (un id riusato verrebbe
rifiutato da arq e il buffer resterebbe orfano).

**3. Guideline nel prompt.** `_RESPONSE_SCHEMA_HINT` ora dice esplicitamente:
i placeholder media sono contenuti che il modello non vede; non fingere di
averli visti; un media da solo **non** è motivo di `escalate_human`; più media
di fila = una sola risposta.

## Conseguenze

- Exactly-once end-to-end: gate d'ingresso (`auto_reply`) per i turni
  sequenziali, claim atomico per quelli concorrenti, dedupe persist-phase per
  i media forzati. Vale anche per il fallback `llm_failed` (una sola cortesia,
  non una per inbound).
- Il debounce torna affidabile e può restare attivo (default 8s): un album di
  foto coalesce in **un** turno LLM già a monte del claim.
- Caso limite accettato: processo che muore tra commit del claim e invio sul
  filo → thread all'operatore senza messaggio al cliente (prima il crash nello
  stesso punto perdeva sia messaggio che takeover). L'inbox merchant lo
  triagia via `handoff_at`.
- Caso limite accettato: `llm_failed` con `escalation.enabled=false` → il
  claim fa comunque il takeover di sicurezza ma l'handler salta l'evento
  analytics (nessuna notifica Realtime); il thread è comunque visibile come
  handoff pendente.
- Test: 4 regression test in `test_uc01_conversation_service.py` (burst → un
  solo messaggio poi silenzio; claim perso → nessun invio/persist/dispatch;
  escalation disabled → risposta LLM; burst video → un solo evento) +
  assertion aggiornate in `test_delivery_debounce.py`.
