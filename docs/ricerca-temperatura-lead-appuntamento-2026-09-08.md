# Temperatura lead → "proponi l'appuntamento quando diventa caldo"

**Data:** 2026-09-08 · **Domanda del cliente:** *"vorrei usare la temperatura del lead così: il bot propone l'appuntamento quando il lead diventa caldo"*

Tutte le affermazioni qui sotto sono verificate sul codice a `main`. Le righe citate sono quelle
osservate il 2026-09-08.

---

## 1. Che cos'è oggi la "temperatura"

Non è un campo e non è il sentiment: **è una funzione pura del punteggio del lead** (`leads.score`, 0-100).

- Il punteggio è ricalcolato **a ogni turno inbound**: `ConversationService` inietta sempre
  un'azione `update_score` sintetica anche quando l'LLM non ne emette una
  — `conversation_service.py:2107-2126`, `_with_score_action` `:2604-2627`.
- È un **UPDATE distruttivo**, senza storico — `repositories/lead.py:86-92`. L'unica traccia del
  "prima" è nelle properties dell'evento analytics.
- Il sentiment (`positive|neutral|negative`, `gpt-5-nano`) è solo **uno dei 13 segnali**, peso +10
  su 100 — `scoring.py:24`.

### Due classificatori divergenti (bug)

| | funzione | soglie | dove |
|---|---|---|---|
| Agente AI / evento | `classify_temperature(score, hot, cold)` | config merchant, default hot **80** / cold **30** | `actions/scoring.py:130` |
| Lavagnetta (automazioni) | `_temperature(score)` | costanti **cablate** hot 80 / warm **40** | `engine.py:119-120, 1681-1686` |

**Un lead a 35 è `warm` per il bot e `cold` per le automazioni.** Il commento a `engine.py:121-122`
lo ammette: *"the inbound path resolves this from config; the automation engine uses the default"*.

---

## 2. Il pezzo che manca è solo il *trigger*

Il segnale "è diventato caldo" **esiste già ed è calcolato con le soglie del merchant**:
l'evento `lead_score_changed` porta `previous_temperature` e `temperature`
— `actions/scoring.py:110-127`.

**Ma non lo ascolta nessuno.** Non è in `EVENT_TO_TRIGGER` (`engine.py:89-103`) né in
`TRIGGER_TYPES` (`models/automation.py:34-52`). I trigger sono 9: `message_received`, `no_answer`,
`booking_created`, `booking_failed`, `lead_dormant`, `crm_lead_created`, `crm_opportunity_created`,
`conversation_escalated`, `conversation_handoff_overdue`.

Tutto il resto c'è già:

- condizioni **`lead_score`** (op numerico) e **`lead_temperature`** (==/!=) — `automations.py:353-359`
- nodo **`ai_reply`** con `objective` libero + `allowed_actions` fra cui `propose_slots`/`book_slot`
  — `automations.py:49-59`, validazione `:199-213`
- guardia anti-ripetizione **`emit_outcome` / `has_outcome`** (negabile dentro un `condition_group`)
- `conversation.playbook.directives` (testo libero, iniettato come *"REGOLE DELLA CONVERSAZIONE
  (istruzione prioritaria e vincolante)"* — `orchestrator.py:914-921`), già esposto in UI
  — `bot-config/sections.ts:135`

---

## 3. Il vincolo che decide tutto il design

**I tool del calendario girano SOLO sul percorso inbound.**

- **Inbound**: c'è un vero loop tool-dentro-il-turno. `check_availability` viene eseguito e
  l'osservazione riappesa con una seconda chiamata al modello — `orchestrator.py:217-238`.
  → l'AI può scrivere **orari veri, in un messaggio unico e naturale**.
- **Automazione (nodo `ai_reply`)**: `run_proactive` **scarta i read tool** e costruisce il prompt
  con `tools_available=False` — `orchestrator.py:398, 417`.
  → il nodo può solo *dire* "ti va di fissare?". Se gli si permette `propose_slots`, arriva una
  **seconda bolla** con testo cablato *"Ecco le prime disponibilità:"* + i primi 3 slot
  — `booking.py:990-992, 1033-1037`; e senza integrazione GHL o `booking.default_calendar_id`
  fa **`return` silenzioso** — `booking.py:958-967`.

**Conseguenza:** la proposta *bella* (orari veri, un solo messaggio) è possibile solo mentre il
lead sta scrivendo. Una proposta "a freddo" sarà sempre più povera.

---

## 4. Una versione debole esiste già in produzione

Per i merchant che hanno configurato `pipeline.qualified_stage_id`:

1. score ≥ `pipeline.advance_threshold` (**default 60**, `auto_advance` default **True**
   — `schema.py:495-500`)
2. → `_with_pipeline_advance_action` inietta `move_pipeline` in modo **deterministico**
   — `conversation_service.py:2131-2141, 2630-2655` (no-op se `qualified_stage_id is None`)
3. → FSM passa a **`CLOSING`** — `state_machine.py:114-116`
4. → hint nel prompt: *"Il lead è vicino alla conversione. **Proponi la prenotazione** o il passo
   successivo concreto. Non divagare."* — `state_machine.py:49-52`

Quindi il bot **già oggi** vira sulla prenotazione a 60 punti, non a 80, e solo se la pipeline è
configurata. Da verificare sul merchant che ha fatto la richiesta prima di promettere una feature.

---

## 5. Le trappole verificate

1. **Il dead end "un attimo che verifico".** Se il prompt ordina di usare `check_availability` ma il
   loop tool non è attivo (`tool_executor is None`, `agent.tool_use_enabled` off, o
   `max_tool_iterations <= 1`), il modello scrive la frase d'attesa, la richiesta viene scartata e
   **il follow-up non arriva mai**. È documentato nel codice stesso — docstring `orchestrator.py:749-753`
   + warning `orchestrator.py:217-231`. Qualunque design a prompt **deve** gattare su
   `tools_available` e sulla presenza di `check_availability` nell'allowlist, non solo su
   `allowed_actions`.
2. **Chi ha già prenotato resta caldo per sempre.** `booking.py:337, 408, 543, 561` forza
   `score=100` dopo la prenotazione. Serve un gate esplicito (`has_outcome: booking_created`,
   o stato FSM `BOOKED`), altrimenti si ripropone l'appuntamento a chi ce l'ha già.
3. **Il punteggio è di un turno indietro.** `update_score` è dispatchato **dopo** l'invio della
   risposta (`conversation_service.py:2035` send, `:2147` dispatch): la proposta parte al turno
   *successivo*. Se il lead diventa caldo con l'ultimo messaggio e poi tace, sul percorso inbound
   non parte **mai**.
4. **Doppio messaggio.** Un'automazione su `message_received` non sa che l'AI ha appena risposto:
   il gate `ai_paused` (`engine.py:1590-1595`) copre takeover/handoff, non "il bot ha appena
   scritto". Dispatcher ogni minuto (`settings.py:180`) ⇒ seconda bolla a ~40-60s. **Da evitare.**
5. **Lo score non è monotono.** Nessuno storico, ricalcolo a ogni turno: 82 → 76 → 84 attraversa
   la soglia più volte. Serve un latch/isteresi, non un semplice confronto.

---

## 6. Raccomandazione

**v1 — percorso inbound, guidato dalla configurazione (S/M, ~1 giorno, nessuna migrazione).**
Nuova chiave `booking.propose_when_hot` (bool, default **False**) + eventuale
`booking.propose_instructions` (testo libero, sul modello di `handoff.instructions`, ADR 0026).
Quando `score >= scoring.hot_threshold`, si **appende** (non si sostituisce) al blocco
"Stato qualificazione" (`orchestrator.py:333-339`) una direttiva che chiede di proporre
l'appuntamento con orari reali. Gate obbligatori: `booking.enabled` (via `caps.booking_enabled`,
`playbook.py:102` — non rileggerlo), `scoring.enabled`, **`tools_available`**,
`check_availability` nell'allowlist effettiva, e stato FSM non in `{BOOKED, ESCALATED, DEAD}`.
Anti-insistenza con un contatore in `conversations.meta` (JSONB, niente migrazione), **non**
forzando lo stato FSM.

> ⚠️ **Non forzare `CLOSING` come marcatore anti-insistenza.** L'hint di `CLOSING` *è* "Proponi la
> prenotazione": scriverlo nel DB rende la proposta **permanente**, e `state_machine.py:104`
> esclude le obiezioni quando lo stato è `CLOSING` — un lead caldo che obietta sul prezzo resta
> sotto "Non divagare". Da `CLOSING` non esiste transizione di ritorno.

**v2 — trigger `lead_became_hot` sulla lavagnetta (M).** Emettitore edge-triggered dentro
`UpdateScoreHandler` (l'edge è già calcolato a `actions/scoring.py:108-122`), con latch a isteresi
in `leads.meta` sul modello di `dormant_fired_for` (ADR 0015) — **assegnazione ORM**, non
`jsonb_set`, perché `merge_content_signals` (`lead.py:120-138`) riscrive `lead.meta` via ORM nello
stesso turno e cancellerebbe un latch scritto in SQL raw. Serve un evento **nuovo**: mappare
`lead_score_changed` così com'è farebbe partire l'automazione a **ogni messaggio** (emit
incondizionato + dedup Redis per `(flow, event_id)`). Copre il caso "si è scaldato e poi è sparito",
con gate anti-collisione (niente emissione se c'è stato un outbound negli ultimi N minuti).

**PR separato — riconciliazione soglie.** `_temperature` deve usare `classify_temperature` con le
soglie del merchant. **Non è un bugfix neutro:**
- tutti i lead 31-39 passano da `cold` a `warm` ⇒ i flussi live con condizione `lead_temperature`
  cambiano comportamento senza che nessuno tocchi la lavagnetta;
- `engine.py:951` alimenta anche il **ModelRouter** (`router.py:106`: `lead_score >= hot_threshold`
  → modello di escalation): un merchant con `hot=50` sposterebbe di colpo i turni proattivi sul
  modello caro. **Cambio di costo.**
Va annunciato, con ADR.

---

## 7. Cosa si può fare **oggi**, senza codice

Funziona perché lo score **è già nel prompt** (`orchestrator.py:333-339`, dietro `scoring.enabled`)
e le azioni di booking sono già permesse di default (`allowed=None` ⇒ tutte — `orchestrator.py:762-772`).

In **Bot → Configurazione → Regole della conversazione** (`conversation.playbook.directives`):

```
Il punteggio interno del lead è nel contesto: è uso interno, non citarlo mai al cliente.
Finché il punteggio è sotto 80, non proporre appuntamenti: fai una domanda di qualifica alla volta.
Quando il punteggio raggiunge o supera 80, smetti di qualificare: verifica la disponibilità reale
con lo strumento e proponi 2-3 orari concreti nello stesso messaggio, chiedendo quale preferisce.
Proponi l'appuntamento una sola volta: se il lead rimanda, riprendi il discorso senza riproporre orari.
Se il lead ha già un appuntamento fissato, non proporne un altro: controlla e conferma quello esistente.
```

**Limiti da dire a voce:** è persuasione, non un interruttore — la soglia 80 è un numero congelato
in un testo, che **non** si aggiorna se il merchant cambia `scoring.hot_threshold`; non c'è nessun
log che dica "ho proposto perché score ≥ 80"; e il modello disobbedirà una volta su N.
Lasciare **"Azioni permesse" vuoto** (selezionarne alcune senza includere `check_availability`
riattiva il dead end del punto 5.1).
