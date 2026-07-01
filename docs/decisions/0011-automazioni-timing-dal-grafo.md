# ADR 0011 — La tempistica delle automazioni di sistema vive nel grafo (lavagnetta)

Data: 2026-07-01 · Stato: accettato · Contesto: audit `docs/audit-automazioni-lavagnetta-2026-07-01.md`

## Contesto

Le 3 automazioni di sistema (`no_answer`, `reactivation`, `booking_reminder`) avevano la
tempistica/cadenza/conteggi nei `bot_configs.overrides` (3 card numeriche), mentre la lavagnetta
(grafo) descriveva **solo il contenuto** del messaggio: `resolve_send_node_at` salta i nodi `wait`
(«the scheduler owns timing»). Risultato: nessuna delle 3 era configurabile end-to-end dal canvas.

Si è scelto l'**approccio A2**: il grafo diventa la **sorgente di verità della tempistica**; gli
scheduler restano gli **executor** (conservano cron, scan candidati, RLS, gate finestra-24h
`decide_outbound`, dedup Redis, optimal-hour). `engine.py` (motore event-driven dei flussi custom)
non viene toccato; i system flow restano scheduler-driven.

## Decisione

### Forma del grafo di un flusso di sistema (catena lineare dal trigger bloccato)

```
[trigger] ──(wait | wait_until_before)?──▶ [send] ──(wait)?──▶ [send] ──▶ …
```

### Blocchi (nuovi/estesi)

- **Trigger config (D1).** Il trigger porta la **soglia iniziale** (trigger → 1° send):
  - `no_answer`: `trigger_config.delay_minutes` (default 120, range 30-480)
  - `lead_dormant`: `trigger_config.days` (default 90, range 30-180)
  - `booking_created`: nessuna soglia (la tempistica è negli `wait_until_before`).
- **`wait` con `unit` (D2).** `config = {value|minutes, unit: minutes|hours|days}`. Normalizzato a
  minuti via `wait_minutes()`. Retro-compat: i nodi esistenti `{minutes: N}` (senza `unit`) = N minuti.
- **`wait_until_before` (D3, nuovo `ACTION_TYPE`).** `config = {anchor: "appointment.start_at",
  hours: 1-168}` — «invia N ore PRIMA dell'appuntamento». Solo per `booking_reminder`.
- **Rendering variabili nel `free_text` (D4).** I placeholder (`{name}`, `{{appointment.datetime}}`)
  ora sono risolti anche per i send testo-libero, non solo per i template.

### Resolver (AR1) — `resolve_send_plan(nodes, edges, context) -> SendPlan`

Cammina la catena (valutando le condizioni atomiche/sincrone come `resolve_send_node_at`) e
**accumula** i `wait`. Restituisce `sends: list[PlannedSend]` dove ogni send porta:
- `delay_minutes`: gap (somma dei `wait`) dal send precedente (o dal trigger per il 1°). **Gap
  per-step incrementale**, non cumulativo — coerente con l'aritmetica `last_*_at` degli scheduler.
- `anchor_hours_before`: ore-prima-appuntamento del `wait_until_before` che precede il send (booking).
- `config`: la config del nodo `send`.

`max_attempts = len(plan.sends)`.

### Scheduler (AR2/AR3) — executor che leggono il piano

Precedenza, per ogni parametro: **grafo (system flow abilitato) → ConfigKey → default built-in**.
- `no_answer`: soglia 1° tentativo = leading-wait OPPURE `trigger.delay_minutes`; intervalli successivi
  = `wait` tra i send; `max_followups = len(sends)`.
- `reactivation`: `dormant_days` = `trigger.days`; `interval_days` = `wait` tra i send; `max_attempts
  = len(sends)`.
- `booking_reminder`: l'action di prenotazione (`booking.py`) legge le ore-prima dal piano
  (`[s.anchor_hours_before …]`) per costruire il `reminder_schedule` per-appuntamento.

I conteggi di scan **hardcoded** (`no_answer` `max_followups=4`, `reactivation` `max_attempts=5`,
filtro repo `reminders_sent < max_followups`) restano i **tetti massimi**: il n. di `send` di un
system flow è cap-pato a quei valori in validazione.

### Vincoli (AR4)

- Nei system flow sono ammessi solo nodi valutabili **sincroni/IO-free** sul path di risoluzione:
  `trigger` (bloccato), `condition` atomiche, `wait`, `wait_until_before`, `send`. **Vietati**
  `ai_check`/`ai_reply`/`set_lead_field`/`human_handoff` (il router li blocca): un `ai_check` dentro
  `resolve_send_node_at` cadrebbe in fail-closed silenzioso.
- Le card numeriche restano come **vista parallela** (round-trip su grafo rinviato a B2). Quando un
  system flow è abilitato, il grafo vince; altrimenti valgono i ConfigKey (compat invariata).

## Conseguenze

- La lavagnetta diventa configurabile end-to-end per le 3 automazioni. Nessun timer lungo su Redis
  (gli scheduler restano cron+DB-counter). `first_contact` resta inerte: nascosto dalla UI finché non
  cablato (trigger `message_received` vive nell'hot path inbound, non in uno scheduler).
- Le card e il grafo possono divergere finché non si fa B2 (migrazione `overrides→grafo`).

## Stato dei limiti

- **[RISOLTO] Promemoria appuntamento — contenuto per-offset.** `appointment_reminder.py` ora
  calcola l'offset che sta scattando (`start_at − reminder_due_at`) e seleziona il nodo `send` con
  il `wait_until_before` corrispondente (`resolve_lifecycle_plan` → indice → `resolve_lifecycle_step`).
  Ogni promemoria (48h/24h/2h) può avere copy diversa. Fallback ad `attempt_index=0` se non c'è grafo
  abilitato o offset corrispondente. `AppointmentReminderCandidate.reminder_due_at` aggiunto.
- **[RISOLTO] `no_answer` free_text `{name}`.** `list_reminder_candidates` porta `Lead.name` →
  `ReminderCandidate.lead_name`; lo scheduler lo passa nel context di `decide_outbound`, quindi
  `{name}`/`{{contact.name}}` rendono anche nel no-answer (come già in reactivation/booking).
- **[FATTO — B2] Card numeriche rimosse; lavagnetta unica sorgente.** Le 3 card in
  `automazioni-panel.tsx` sono state eliminate: le automazioni si configurano solo dai blocchi. I
  flussi di sistema nascono **attivi** (`ensure_system_automations` → `enabled=True`, tranne
  `first_contact`) con un grafo di default che rispecchia i default di config (`_DEFAULT_SYSTEM_GRAPH`),
  così "attivo di default" = comportamento storico. L'interruttore sulla lavagnetta è l'unico on/off.
  Migrazione `0043_enable_system_flows`: elimina i flussi di sistema ancora al default intoccato +
  disabilitati (2 nodi, send di default) così vengono riseminati attivi; i flussi personalizzati dal
  merchant restano intatti. I ConfigKey restano solo come fallback quando un flusso è disabilitato.
