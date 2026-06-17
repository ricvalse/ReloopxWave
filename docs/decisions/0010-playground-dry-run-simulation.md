# ADR 0010 — Playground dry-run: simulazione dei tool + conversazione realistica

Status: Accepted (2026-06-17)

## Context

Con ADR 0009 il Playground (UC-08) è diventato una preview fedele di *prompt* e
*impostazioni* del turno WhatsApp reale, ma restava "muto" sui **tool**: l'LLM
emette le azioni (`book_slot`, `move_pipeline`, `update_score`, `escalate_human`)
e venivano mostrate solo come metadati grezzi, mai eseguite. Inoltre il
playground era **stateless per turno** (`lead_score=0` fisso, nessun sentiment,
nessuna identità del lead), quindi una conversazione multi-turno non "evolveva"
come quella reale.

Gli handler reali non sono riusabili nel playground: ognuno apre la propria
sessione DB, costruisce il proprio client GoHighLevel e scrive su righe reali
(`lead`, `conversation`) — che nel playground non esistono (`lead_id=None`).

## Decision

Aggiungere uno **strato di simulazione puro** (dry-run): i tool "fanno qualcosa"
in simulazione, con **zero effetti reali**, e il playground diventa il più simile
possibile a una conversazione cliente reale.

### 1. Simulatore puro (`ai_core/playground_sim.py`)
`simulate_turn(...)` prende le azioni dell'LLM + lo stato simulato + il sentiment
del turno e produce: eventi leggibili ("cosa farebbe il bot"), lo stato evoluto,
e le bolle extra (es. conferma prenotazione). **Riusa la logica pura** di
produzione (no duplicazione): `derive_signals_from_llm_payload` +
`derive_conversation_signals` + `score_lead` + `classify_temperature`
(`actions/scoring.py`, `scoring.py`), e `format_booking_confirmation`
(estratta da `actions/booking.py`).

### 2. Stato del lead evolutivo (client↔server)
`PlaygroundLeadState` (score, sentiment, nome/email, pipeline stage, booked,
escalated, turn_count) viaggia nel request/response; il backend resta stateless.
Il `lead_score` portato alimenta l'orchestrator → l'escalation `hot_lead` può
scattare man mano che il lead si scalda, come in produzione.

### 3. Sentiment fedele a ogni turno
`PlaygroundRunner` chiama `SentimentAnalyzer` (gpt-5-nano) a ogni turno
(read-only); il sentiment del turno *precedente* (nello stato) adatta il prompt,
quello corrente viene salvato nello stato per il turno dopo — identico al reale.

### 4. Consegna stile WhatsApp
La risposta viene splittata in bolle (`split_into_bubbles`) con ritardi per bolla
(`compute_typing_delay_s`) risolti dalla config cascade; il frontend mostra
"sta scrivendo…" e rivela le bolle una alla volta. La conferma di prenotazione
arriva come bolla finale, come il messaggio separato che invierebbe il reale.

### 5. Zero persistenza (invariante)
Il dry-run **non** scrive su DB, **non** chiama GHL, **non** invia WhatsApp,
**non** emette analytics. Solo letture config + chiamate LLM read-only.

## Rejected alternatives

- **Eseguire gli handler reali con fake (GHL/sender mockati).** Gli handler sono
  troppo intrecciati con righe DB reali (lead/conversation) e aprono sessioni
  proprie; servirebbe un lead/conversazione fittizi persistiti. Più fragile e a
  rischio di side-effect rispetto a un simulatore puro che replica solo l'esito
  osservabile.
- **Persistere score/analytics "perché sicuro".** Inquinerebbe i dati reali del
  merchant (dashboard, analytics) con turni di prova. Dry-run = zero scritture.
- **Override score=100 su prenotazione** (come fa il `BookSlotHandler`). In
  produzione l'`update_score` always-on è dispatchato dopo `book_slot` e
  sovrascrive quel 100 nello stesso turno: lo score finale è quello cumulativo.
  Il simulatore usa quindi solo lo scoring always-on (più fedele all'esito).

## Consequences / note di fedeltà

- **Prenotazione**: assunta sempre riuscita (nessun GHL per verificare gli slot)
  → mostra "Perfetto, ho prenotato…" e cattura nome/email dai `contact_fields`.
- **Nome/email**: catturati dalle azioni per far evolvere `has_name`/`has_email`
  nello scoring (in prod oggi si sincronizzano solo verso GHL): scelta consapevole
  per realismo della conversazione.
- **Escalation**: setta lo stato `escalated` e lo mostra; il playground lascia
  comunque proseguire (è una preview), mentre in reale il bot verrebbe silenziato.
- **History LLM pulita**: la `history` inviata contiene il `reply_text` completo
  come una sola voce assistant; bolle ed eventi sono solo presentazione.
- Copertura: nuovo `test_playground_sim.py` + estensione `test_uc08_playground.py`
  (stato che si propaga, sentiment, scoring/booking/pipeline/escalate). `generated.ts`
  rigenerato per `PlaygroundStateModel`/`PlaygroundBubble`/`PlaygroundEvent`.
