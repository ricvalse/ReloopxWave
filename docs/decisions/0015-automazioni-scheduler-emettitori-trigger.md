# ADR 0015 — Automazioni: lo scheduler è solo un emettitore di trigger (edge-triggered); la lavagnetta possiede cadenza e contenuto

Data: 2026-07-01 · Stato: proposto · Contesto: richiesta owner — «vorrei che la fine del system flow sia il trigger che poi viene usato nelle automazioni».

## Contesto

Oggi esistono **due mondi** di automazioni (vedi [[0011-automazioni-timing-dal-grafo]]):

- **Flussi custom** (`automation_flows.system_key IS NULL`): event-driven. Il cron `automation_dispatch`
  (`engine.py:150`) coda gli `analytics_events` recenti, li mappa a un trigger via `EVENT_TO_TRIGGER`
  (`engine.py:70`) e fa fan-out di job `automation_run` verso i flussi abilitati su quel trigger. Il
  motore (`_walk`, `engine.py:314`) esegue il grafo e realizza le attese multi-step come **catena di
  job ARQ differiti** (`_defer_by`, `engine.py:293-301`).
- **Flussi di sistema** (`system_key` ∈ `no_answer|reactivation|booking_reminder|first_contact`):
  scheduler-driven. Tre cron (`followup_no_answer` ogni 15m, `reactivate_dormant_leads` 09:00,
  `send_appointment_reminders` ogni 30m) **rilevano la condizione, risolvono il grafo, gestiscono la
  cadenza e inviano** loro stessi. Il motore event-driven li **esclude** esplicitamente
  (`list_enabled_by_trigger` filtra `system_key IS NULL` in `repositories/automation.py:112`;
  `automation_run` skippa `system_key is not None` in `engine.py:249`).

La dicotomia è la fonte di complessità: trigger bloccato, colonna `system_key`, seeding speciale,
special-casing FE/API, e il bridge `workers/automation/lifecycle.py`. L'owner vuole **un modello
unico**: `no_answer` / `lead_dormant` / `booking_created` sono trigger normali; un'automazione qualsiasi
sulla lavagnetta, agganciata a quel trigger, possiede **tutta** la logica di risposta (contenuto +
cadenza). Lo scheduler si riduce a **rilevare la condizione ed emettere il trigger**.

**Il motore sa già fare la parte difficile.** Verificato: i wait durevoli multi-step esistono già come
catena di job ARQ differiti (`engine.py:291-363`). Quindi NON costruiamo un flow-runner da zero. Il
lavoro reale è un altro: **convertire la detection degli scheduler da *level-triggered* a
*edge-triggered***, senza il quale il nuovo modello genera cadenze duplicate.

### Il problema di idempotency (perché serve questo ADR)

Gli scan degli scheduler sono **level-triggered**: ritornano lo stesso soggetto a **ogni tick** finché
la condizione è vera (`conversation.py:124` «conversation on every tick»; `lead.py:178`). Oggi i
contatori fanno **doppio dovere**:

- `no_answer`: `conversation.meta.reminders_sent` + `last_reminder_at` (`conversation.py:136,149-150,325`)
- `reactivation`: `lead.meta.reactivation_attempts` + `last_reactivation_at` (`lead.py:207-208,276-296`)

= posizione nella cadenza **e** anti-duplicato. Se spostiamo la cadenza nel grafo ma lasciamo lo scan
level-triggered che emette un trigger a ogni tick, ogni emissione ha un `event.id` diverso → il dedup
di dispatch `{auto.id}:{ev.id}` (`engine.py:198`) non li collassa → **N cadenze sovrapposte per lead**.

## Decisione

### D1 — Ancora di episodio (edge-detection via high-water-mark)

Lo scheduler emette il trigger **una sola volta per episodio**, confrontando il **timestamp che guida
la condizione** con un'**ancora** salvata sul soggetto. L'ancora sfrutta timestamp che **già esistono**
e che avanzano **solo** quando il lead ri-ingaggia — quindi il re-arm è automatico, senza bisogno di
"pulire" nulla a fine flusso.

| Trigger | Timestamp guida (inizio episodio) | Ancora (nuova, in `meta`) |
|---|---|---|
| `no_answer` | `conversation.last_inbound_at` (`conversation.py:110-121` — «start of the current silence») | `conversation.meta.no_answer_fired_for` |
| `lead_dormant` | `last_interaction_at = MAX(Conversation.last_message_at)` (`lead.py:186-194`) | `lead.meta.dormant_fired_for` |
| `booking_created` | — (evento già puntuale, vedi D4) | — |

Regola di emissione (per `no_answer`; `lead_dormant` è speculare):

```
EMETTI lead.no_answer  SE  last_message_at < idle_cutoff            -- condizione già esistente
                       AND (no_answer_fired_for IS NULL
                            OR no_answer_fired_for < last_inbound_at) -- edge: episodio nuovo
poi  SET no_answer_fired_for = last_inbound_at
```

Quando il lead risponde, `last_inbound_at` avanza → il prossimo silenzio ri-arma da solo. Stesso
episodio → soppresso. **I vecchi contatori (`reminders_sent`, `reactivation_attempts`,
`last_reminder_at`, `last_reactivation_at`) vengono rimossi**: la cadenza non è più tracciata dallo
scheduler, vive nel grafo (D3).

### D2 — Eventi-trigger dedicati

Emettere eventi dedicati, **subject = lead/conversation**, al momento del crossing:

- `lead.no_answer` → trigger `no_answer`
- `lead.dormant` → trigger `lead_dormant`

e aggiornare `EVENT_TO_TRIGGER` (`engine.py:70-76`):

```python
EVENT_TO_TRIGGER = {
    "message.received": "message_received",
    "booking.created":  "booking_created",
    "booking.failed":   "booking_failed",
    "lead.no_answer":   "no_answer",     # NUOVO (era: "reminder.sent")
    "lead.dormant":     "lead_dormant",  # NUOVO (era: "lead_reactivation.sent")
}
```

`reminder.sent` / `lead_reactivation.sent` **escono** dalla mappa: tornano a essere **puri record KPI**
(oggi sono sovraccarichi — servono sia da metrica sia da segnale trigger). Così un record analytics non
fa più scattare una cadenza.

### D3 — La cadenza è strutturale nel grafo

La sequenza multi-tentativo diventa **nodi reali** sulla lavagnetta, non un contatore runtime:

```
[trigger no_answer] → [send] → [wait 1g] → [send] → [wait 1g] → [send]
```

`max_attempts` = numero di nodi `send`. Il tetto di validazione (storicamente `no_answer` 4,
`reactivation` 5) resta come **cap** sul numero di `send` ammessi, non come contatore. Il contenuto
viene **solo** dai nodi `send.free_text`/template (invariante [[0014-automazioni-off-di-default]]).

### D4 — `booking_created` è già edge; nessuna idempotency nuova

`booking.created` è già emesso **una volta** al momento della prenotazione (`booking.py`), quindi è
naturalmente edge-triggered. Il `booking_reminder` diventa:

```
[trigger booking_created] → [wait_until_before 24h] → [send] → [wait_until_before 2h] → [send]
```

Non serve scan né ancora. Serve **solo** il supporto motore a `wait_until_before` (vedi D6.2).

### D5 — Guardia "ancora rilevante" al resume (annulla cadenze stantie)

Una cadenza differita va **annullata a metà** se l'episodio è finito prima del prossimo send (il lead
ha risposto; l'appuntamento è stato annullato/spostato). Oggi lo fa implicitamente il re-scan dello
scheduler; nel modello event-driven i job differiti sono già in coda, quindi:

- Il job differito **porta l'ancora di episodio** (il valore del timestamp guida al momento del trigger),
  aggiunta ai parametri di `automation_run`/all'enqueue dei deferral (`engine.py:293-301`).
- Al resume, prima di eseguire i `send`, il motore **ri-valuta** (estende `_resolve_context`,
  `engine.py:919`, che già ricalcola la finestra 24h da `last_inbound_at` a `engine.py:944`):
  - `no_answer` → ABORT se `conversation.last_inbound_at > ancora` (il lead ha risposto)
  - `lead_dormant` → ABORT se `last_interaction_at > ancora` (il lead ha ri-ingaggiato)
  - `booking_created` → ABORT se l'appuntamento è cancellato o `start_at` è cambiato vs l'ancora

Semantica identica a quella che gli scheduler avevano implicitamente, resa esplicita e per-step.

### D6 — Lacune di motore da chiudere (indipendenti, PR1)

1. **Unità del `wait` ignorata.** `_walk` legge `minutes` grezzo (`engine.py:357-358`) senza
   `wait_minutes()` (`automations.py:102-109`): un nodo `{value:7, unit:"days"}` differisce **7 minuti**.
   Bloccante per `reactivation` (intervalli in giorni). Fix: usare `wait_minutes(node.config)`.
2. **`wait_until_before` è un no-op nel motore.** `_walk` gestisce solo `type == "wait"`; un
   `wait_until_before` cade in `_do_action` e si estende **subito** ai successori. Aggiungere il ramo:
   `_defer_by = (appointment.start_at − hours_before) − now` (skip se già passato). Richiede esporre
   `start_at` in `_resolve_context` per subject `appointment`. **È l'unica vera aggiunta di motore.**
3. **Continuazioni senza dedup.** I job post-wait (`engine.py:293-301`) non passano `dedup=`: un retry
   ARQ può ri-inviare quel segmento. Aggiungere una key deterministica
   (es. `f"{automation_id}:{subject_id}:{sorted(start_keys)}:{fire_epoch}"`).

### D7 — Rimozione del concetto `system_key`

- **DB**: migrazione forward che droppa colonna+indici `system_key` (supersede `0028`/`0043`). Le righe
  esistenti sopravvivono come automazioni normali (portano già `trigger_type`, `0028:63-69`).
- **Backend**: rimuovere `SYSTEM_AUTOMATION_KEYS`, `SYSTEM_TRIGGER_TYPE`, `SYSTEM_FLOW_NAMES`,
  `_DEFAULT_SYSTEM_GRAPH`, `ensure_system_automations`, `get_by_system_key`, il filtro
  `system_key.is_(None)` (`repositories/automation.py:112`), lo skip `engine.py:249`, le guardie
  edit/delete e `is_system` in `routers/automations.py`. **Eliminare `workers/automation/lifecycle.py`**.
- **Frontend**: de-threadare `isSystem` in `automation-editor.tsx` + `automazioni-panel.tsx` (trigger
  sbloccato, palette piena, lista unica). Rigenerare il client typed.
- **Seeding**: i 4 flussi NON spariscono → diventano **template seminati come automazioni normali**,
  `enabled=False`, con grafo di cadenza reale (D3) e contenuto solo da canvas (D4).

### D8 — Riduzione degli scheduler a emettitori

I tre scheduler (`no_answer.py`, `reactivation.py`, `appointment_reminder.py`) perdono: risoluzione
config, aritmetica `next_attempt`+cap, gate elapsed-time, `resolve_lifecycle_*`, `decide_outbound`,
send, `record_*_sent`, dedup Redis. Tengono **solo** lo scan di detection + l'emissione edge-triggered
(D1/D2). `appointment_reminder.py` si riduce a nulla (il trigger è `booking.created` alla prenotazione).

## Conseguenze

- Modello unico: ogni automazione = trigger + grafo. La cadenza è **visibile** sulla lavagnetta
  (coerente con la filosofia «tutto dalla lavagnetta» di [[0014-automazioni-off-di-default]]).
- Invarianti ADR 0014 **preservate**: seeding `enabled=False`, `decide_outbound` senza `fallback_text`
  (nodo `send` vuoto → skip `no_content`), contenuto solo da canvas.
- Timer lunghi (reactivation fino a 30 giorni) diventano job ARQ differiti anziché re-scan giornaliero:
  semantica di fallimento diversa (perdita job = perdita cadenza) — mitigabile con un cron di
  riconciliazione (opzionale).

## Rollout (3 PR)

- **PR1** — motore: D6 (unità wait, `wait_until_before`, dedup continuazioni). Indipendente, basso rischio.
- **PR2** — D1/D2/D5 (ancore, eventi dedicati, guardia al resume) **dietro feature flag**, con i send
  degli scheduler ancora attivi. Qui si valida l'idempotency edge-triggered.
- **PR3** — D7/D8 (rimozione `system_key` + scheduler→emettitori) **atomico**: spegne i send scheduler e
  accende l'event-dispatch nello stesso PR, altrimenti doppio invio.

## Decisioni aperte

1. **Backfill ancore alla migrazione.** Un lead a metà cadenza nel vecchio sistema ha ancora NULL →
   verrebbe ri-triggerato una volta. Accettare il transiente una-tantum, o backfillare `*_fired_for`
   dai `last_reminder_at`/`last_reactivation_at` esistenti. Dipende da **quanti merchant prod hanno
   flussi di sistema già seminati/attivi** (dato da verificare prima).
2. **`first_contact` su `message_received`.** Il trigger vive nell'hot-path inbound e scatterebbe a
   **ogni** messaggio, non solo al primo. Serve una condizione "primo messaggio del lead" nel grafo (o
   un trigger dedicato). Fuori dal cuore di questo ADR — oggi `first_contact` è inerte/nascosto.
3. **Riconciliazione timer lunghi.** Se serve robustezza sui differiti multi-settimana, un cron di
   riconciliazione che ri-arma cadenze perse. Da valutare dopo PR2.

## Riferimenti

- Inverte il confine di [[0011-automazioni-timing-dal-grafo]] (scheduler executor → scheduler emettitore).
- Preserva [[0014-automazioni-off-di-default]] (off di default, contenuto solo da canvas).
- Invarianti booking (timezone, refresh-token GHL, `escalate_human`): vedi ADR [[0007-ghl-marketplace-agency-install]] e la sezione booking di `reloop-ai-architettura.md`.
