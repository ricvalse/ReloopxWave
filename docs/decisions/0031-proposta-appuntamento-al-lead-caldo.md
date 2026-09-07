# ADR 0031 — Il bot propone l'appuntamento quando il lead diventa caldo

**Data:** 2026-09-08 · **Stato:** accettata (v1 implementata) · **Contesto:**
richiesta merchant — *"vorrei usare la temperatura del lead così: il bot propone
l'appuntamento quando il lead diventa caldo"*

Ricerca a monte: `docs/ricerca-temperatura-lead-appuntamento-2026-09-08.md`.

---

## Contesto

"Temperatura" non è un campo: è una funzione del punteggio del lead
(`leads.score`, ricalcolato ogni turno inbound). La lavagnetta ha già le
condizioni `lead_score` e `lead_temperature`, e il nodo `ai_reply` può già
dispatchare `propose_slots`. Quello che mancava era il momento in cui dire al bot
*"adesso proponi"*.

Il vincolo che ha deciso tutto il resto:

| percorso | i tool del calendario girano? | cosa può fare |
|---|---|---|
| **inbound** (il lead scrive) | **sì** — loop tool-dentro-il-turno, `orchestrator.py:217-238` | legge le disponibilità vere e le scrive nel testo, **un messaggio solo** |
| **automazione** (`ai_reply`) | **no** — `run_proactive` scarta i read tool, `tools_available=False` (`orchestrator.py:398,417`) | manda testo generico; con `propose_slots` una **seconda bolla** con testo cablato (`booking.py:1033-1037`) |

Sono due prodotti diversi, non due fasi.

## Decisione

**v1 = percorso inbound, guidato dalla configurazione.** Quando il punteggio
supera `scoring.hot_threshold`, il system prompt riceve un obiettivo esplicito di
proporre l'appuntamento. Il modello, nel loop già esistente, chiama
`check_availability` e propone orari veri in un messaggio unico e naturale.

Tre chiavi nuove, tutte sotto `booking.`:

| chiave | default | cosa fa |
|---|---|---|
| `booking.propose_when_hot` | `False` | l'interruttore (ADR 0014: niente feature accese di default) |
| `booking.propose_instructions` | `None` | testo libero del merchant su *come* proporre |
| `booking.propose_max_per_conversation` | `1` | tetto anti-insistenza |

**La soglia è riusata, non duplicata.** `scoring.hot_threshold` è già in UI ed è
già quella che governa il badge "Caldo" dell'inbox: introdurre una seconda soglia
avrebbe significato due numeri da tarare e due definizioni di "caldo".

## Perché così e non altrimenti

### Il gate sulla disponibilità dei tool sta nell'orchestrator, non nel servizio

`_build_messages` è l'unico punto che sa se il loop girerà davvero in questo turno
(`orchestrator.py:185-188`). Il servizio decide *se* spingere (cancelli sullo
stato del lead), l'orchestrator decide *cosa dire* (cancello sul turno).

Non è un dettaglio di stile: ordinare al modello di chiamare `check_availability`
quando i tool non ci sono produce il vicolo cieco che `render_schema_hint`
documenta — scrive "un attimo che verifico", la richiesta viene scartata prima
del dispatcher, e **il follow-up non arriva mai**. Quando i tool mancano, o quando
l'allowlist del playbook non contiene `check_availability`, la direttiva cambia:
niente orari, si chiede una fascia di preferenza.

### Non si forza lo stato FSM `CLOSING`

Era la mossa apparentemente ovvia — l'hint di `CLOSING` è già *"Proponi la
prenotazione o il passo successivo concreto. Non divagare."* — ed è sbagliata:

- quell'hint **è** la spinta, quindi scrivere `CLOSING` nel DB rende la proposta
  permanente invece di fermarla;
- `state_machine.py:104` esclude le obiezioni quando lo stato è `CLOSING`: un lead
  caldo che obietta sul prezzo resterebbe sotto "Non divagare";
- da `CLOSING` non esiste transizione di ritorno.

L'anti-insistenza è quindi un contatore esplicito in `conversations.meta`
(`booking_nudge_count`), non uno stato.

### Il contatore conta le iniezioni, non i successi

Se contasse solo le proposte andate a buon fine, il bot riproverebbe a ogni turno
finché il modello non cede — esattamente l'assillo che il tetto esiste per
evitare. Il prezzo: se il modello ignora la direttiva, quel turno è comunque
consumato. Il merchant può alzare il tetto a 2-3.

### In append al blocco `move_pipeline`, non in sostituzione

Avanzamento pipeline e proposta di appuntamento sono cose diverse: sostituire
spegnerebbe `move_pipeline` proprio sui lead migliori.

### Il lead che ha già prenotato

`actions/booking.py` forza `score=100` dopo la prenotazione: chi ha prenotato
resta sopra qualsiasi soglia per sempre. Il cancello sullo stato FSM `BOOKED`
(più `ESCALATED` e `DEAD`) è quello che impedisce di riproporre un appuntamento a
chi ce l'ha già. **Resta scoperto** chi ha prenotato fuori dal flusso (telefono,
GHL, operatore): lo stato non è `BOOKED` e il bot può riproporre. Il fix vero —
smettere di forzare 100 — è fuori da questo ADR.

## Conseguenze

- Nessuna migrazione: `conversations.meta` è già JSONB NOT NULL; la config vive in
  `bot_configs.overrides`.
- `RESOLVED_CACHE_KEY` → `__resolved_v5__` (chiavi nuove nello schema).
- **La spinta parte un turno dopo** il superamento della soglia: `update_score` è
  dispatchato dopo l'invio della risposta, quindi `rc.lead_score` è il punteggio
  precedente. Chi si scalda con l'ultimo messaggio e poi tace non è servito qui.
- Costo: sui lead caldi il turno usa il loop dei tool (2 chiamate al modello
  invece di 1), e `router.py:106` instrada già i lead caldi sul modello grande.

## Non fatto qui, deliberatamente

### v2 — trigger `lead_became_hot` sulla lavagnetta

Serve al caso che v1 non copre: **il lead si scalda e poi sparisce**.

L'edge è già calcolato: `lead_score_changed` porta `previous_temperature` e
`temperature`, con le soglie del merchant (`actions/scoring.py:108-127`). Manca
solo chi lo ascolti.

Piano:

1. **Evento nuovo, non `lead_score_changed` mappato.** Quest'ultimo è emesso a
   ogni turno scorato e il dedup Redis è per `(flow, event_id)`: mapparlo così
   com'è farebbe partire l'automazione a **ogni messaggio**. Emettere `lead.hot`
   solo sulla transizione `previous_temperature != "hot" and temperature == "hot"`.
2. **Latch con isteresi** in `leads.meta` (`hot_fired_for`), sul modello di
   `dormant_fired_for` (ADR 0015) — lo score non è monotono, 82→76→84 riattraversa
   la soglia. Nuova chiave `scoring.hot_rearm_margin`.
   **Attenzione:** scriverlo con `jsonb_set` raw sarebbe un bug. `merge_content_signals`
   (`repositories/lead.py:120-138`) riassegna `lead.meta` via ORM nello stesso
   turno e cancellerebbe il latch. Usare assegnazione ORM, e `(lead.meta or {})`.
3. Riga in `EVENT_TO_TRIGGER` (`engine.py:~96`), voce in `TRIGGER_TYPES`
   (`models/automation.py:34-52`), voce in `TRIGGER_DEFS`
   (`automation-nodes.tsx:48-99`, array letterale, va toccato a mano).
   `/automations/catalog` espone `triggers` come `list[str]`: **niente drift OpenAPI**.
4. **Gate anti-collisione**: l'evento nasce dentro il turno inbound e il
   dispatcher gira ogni minuto — senza guardia il lead riceve la risposta del bot
   e 40 secondi dopo "ti va di fissare un appuntamento?". Non emettere se c'è
   stato un outbound sulla conversazione negli ultimi N minuti.
5. Nel flusso tipo, condizione `has_outcome: booking_created` negata subito dopo
   il trigger.

Aspettativa da fissare col merchant: su questo percorso la proposta è **più
povera** (niente orari reali nel testo, o una seconda bolla).

### PR separato — riconciliazione delle soglie

`_temperature` (`engine.py:1681`) usa costanti cablate hot 80 / **warm 40** e
ignora la config; `classify_temperature` usa le soglie del merchant (80/**30**).
Un lead a 35 è `warm` per il bot e `cold` per la lavagnetta.

**Non è un bugfix neutro**, e per questo non è in questo PR:

- i lead 31-39 passano da `cold` a `warm`: ogni flusso live con condizione
  `lead_temperature` cambia comportamento senza che nessuno tocchi la lavagnetta;
- `engine.py:951` alimenta anche il **ModelRouter** (`router.py:106`:
  `lead_score >= hot_threshold` → modello di escalation): un merchant con
  `hot=50` sposterebbe di colpo i turni proattivi sul modello caro — **cambio di
  costo**;
- `_resolve_context` non ha un `ConfigResolver` (in `engine.py` ce n'è uno solo, a
  `:943`): risolvere lì le soglie aggiunge due `await` per **ogni** job
  automazione, su un pool da 15 slot che ha già dato `EMAXCONNSESSION`.

Va fatto con cache, annunciato, e con un ADR suo.

## Prima di accendere la feature su un merchant

1. `pipeline.qualified_stage_id` configurato? Allora **a 60 punti**
   (`pipeline.advance_threshold`) il bot già vira sulla prenotazione via
   `move_pipeline` → FSM `CLOSING`: forse basta spostare quella soglia.
2. `booking.default_calendar_id` + integrazione GHL presenti? Senza, niente orari.
3. `agent.tool_use_enabled` on e `agent.max_tool_iterations >= 2`? (default `True`
   e `3`). Se no, la direttiva degrada alla variante senza orari — corretta, ma
   non è quello che il merchant si aspetta.
4. `bot.auto_reply_enabled`? Default **False**: se è off il bot non risponde
   affatto e la feature è inerte.
