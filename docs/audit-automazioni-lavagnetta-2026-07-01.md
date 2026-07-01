# Audit — Configurare le 3 automazioni di sistema dalla lavagnetta (canvas)

> Data: 2026-07-01 · Stato: audit verificato (multi-agente + critico avversariale). Nessun codice ancora scritto.
> Richiesta: «voglio poter configurare le automazioni (Nessuna risposta / Riattivazione dormienti / Promemoria appuntamento) dalla lavagnetta con i blocchi che abbiamo; fai un audit sui blocchi necessari e, se mancano, aggiungili».

## 1. Sintesi

Oggi esistono **due meccanismi paralleli e separati** per `no_answer`, `reactivation`, `booking_reminder`:

- **(a) 3 card numeriche** in `automazioni-panel.tsx` → scrivono `bot_configs.overrides` → governano **tempistica / cadenza / conteggi**, lette dagli scheduler cron via `ConfigResolver`.
- **(b) Flussi di sistema sul canvas** (`AutomationFlow.system_key`) → il canvas serve **solo** a risolvere il **contenuto** del send N-esimo. `resolve_send_node_at` (`ai_core/automations.py:332-380`) **salta i nodi `wait`** («the scheduler owns timing») e conta solo i `send`.

**Conclusione:** nessuna delle 3 automazioni è oggi esprimibile *end-to-end* sul canvas. Sul grafo si disegna il messaggio, ma **tempistica, soglie e numero di tentativi vivono nei ConfigKey**, non nei blocchi. Per realizzare davvero «configuro tutto dalla lavagnetta» servono **blocchi UI nuovi** *e* il **wiring** che faccia leggere la tempistica agli scheduler dal grafo.

## 2. Stato per automazione

### `no_answer` — Nessuna risposta
| Blocco necessario | Stato | Gap |
|---|---|---|
| Trigger "silenzio lead" con `delay_minutes` editabile | parziale | tipo esiste (`models/automation.py:36-42`), ma `TRIGGER_DEFS.fields=[]` (`automation-nodes.tsx:41`); nei system flow il gruppo trigger è nascosto (`automation-editor.tsx:236`) |
| `wait` tra tentativi (35 min / 1440 min) | parziale | `wait` inerte per i system flow (saltato, `automations.py:377-379`); timing da `NO_ANSWER_FIRST/SECOND_REMINDER_MIN` (`no_answer.py:89-104,126-129`) |
| Invio 1° / 2° reminder (`send`) | parziale | il flusso seedato ha **un solo** `send` → il 2° ricade sui `REMINDER_TEXTS` built-in (`no_answer.py:45-48`) |
| Max follow-up (default 2, range 1-4) | parziale | nessun nodo loop/stop-after-N (grafo aciclico, `automations.py:117-118`); cap in `NO_ANSWER_MAX_FOLLOWUPS`; sul grafo è implicito = n. di `send` |
| Wiring: scheduler legge timing dal grafo | mancante | gap centrale (vedi §4) |

### `reactivation` — Riattivazione dormienti
| Blocco necessario | Stato | Gap |
|---|---|---|
| Trigger `lead_dormant` con soglia `days` (30-180) | parziale | tipo esiste (`automation.py:39`), `fields=[]`; `reactivation.py:94-97` legge `REACTIVATION_DORMANT_DAYS`, non `trigger_config` |
| `wait` in **giorni** tra tentativi (3-30) | parziale | `wait` solo in minuti (`automation-nodes.tsx:186-189`); timing da `REACTIVATION_INTERVAL_DAYS` |
| "Ripeti fino a N" (`max_attempts` 1-5) | mancante | cap in `REACTIVATION_MAX_ATTEMPTS`, su contatore persistito (`reactivation.py:104-110`) |
| `send` contenuto riattivazione | esiste* | OK; dormienti fuori finestra 24h → serve template (*vedi nota free_text §3) |
| Scheduler legge timing+conteggio dal grafo | mancante | gap abilitante centrale |

### `booking_reminder` — Promemoria appuntamento (l'outlier strutturale)
| Blocco necessario | Stato | Gap |
|---|---|---|
| Trigger `booking_created` | parziale | esiste, ma come system flow è scheduler-driven, mai event-driven (`engine.py:245-246`) |
| **Blocco "attendi fino a X ore PRIMA dell'appuntamento"** | **mancante** | non esiste: il solo `wait` è in minuti **dopo il trigger**, non "X ore prima di un'ancora futura". È il blocco concettualmente nuovo richiesto |
| `send` con `{{appointment.datetime}}` | parziale | con **template** + `variable_mapping` già risolto oggi (`appointment_reminder.py:103` inietta `appointment.datetime`); col **free_text** no (§3) |
| Catena multi-reminder (fino a 5) come nodi | mancante | i 5 valori vivono in `BOOKING_REMINDER_SCHEDULE` materializzati per-appuntamento (`appointment.py:19-40`); grafo seedato = 1 solo `send`; `resolve_lifecycle_step` chiamato sempre con `attempt_index=0` (`appointment_reminder.py:89-98`) |
| Esecutore per-appuntamento + ri-ancoraggio su reschedule | mancante | oggi cron + colonna `reminder_due_at` + `mark_reminded` (`appointment.py:221-313`) |

## 3. Blocchi DISCRETI da aggiungere (UI + plumbing locale)

| # | Blocco | Dove (FE / BE) | Sforzo |
|---|---|---|---|
| **D1** | Config editabile sui trigger temporali (`no_answer.delay_minutes`, `lead_dormant.days`) | FE: `fields` nei `TRIGGER_DEFS`, rendering trigger in `NodeConfigPanel`, ramo trigger in `defaultConfig`, smettere di nascondere il gruppo trigger nei system flow mantenendo il **tipo** locked. BE: range in `validate_graph` (plumbing `trigger_config` già c'è, `automations.py:121`, `routers/automations.py:288`) | S-M |
| **D2** | Selettore **unità** sul `wait` (minuti/ore/giorni) | FE: campo `unit` su `wait` + `nodeSummary` + `defaultConfig`. BE: `_action_config_errors` accetta unità + normalizza a minuti; `engine.py:353` → `timedelta` | S |
| **D3** | **Nuovo blocco "attendi fino a X ore prima dell'appuntamento"** (`wait_until_before`, `anchor=appointment.start_at`, `hours` 1-168) | FE: node def + campo ore. BE: nuovo `ACTION_TYPE` (solo tupla JSONB, niente migration tabella) + ramo in `_action_config_errors` + supporto engine/scheduler per offset assoluto | M |
| **D4** | **Rendering variabili nel `free_text` dei nodi `send`** *(scoperto dal critico)* | BE: oggi `decide_outbound` sostituisce i placeholder **solo per i template**; per `MODE_TEXT` ritorna `step.free_text` **verbatim** → "Ciao {name}" / "{{appointment.datetime}}" partono **letterali**. Il `.replace("{name}", …)` in `engine.py:455-457` è **dead code** (decide_outbound preferisce `step.free_text`). Serve risoluzione placeholder nel free_text. | S-M |
| **D6** | Range di compliance in `validate_graph` (guardrail anti-spam/24h) | BE: portare i min/max Pydantic (`schema.py:289-300,405`) sui nodi `wait`/trigger: `first_reminder_min` 30-480, `second_reminder_min` **720-2880**, `dormant_days` 30-180, `interval_days` 3-30, `max_followups` 1-4, `max_attempts` 1-5, `reminder_schedule` max 5. **Obbligatorio se il timing passa al grafo** | S |

> **D3** e **D4** sono **non evitabili** in ogni scenario: senza D3 la tempistica di `booking_reminder` non può vivere sul canvas; senza D4 ogni testo libero personalizzato sul canvas viene inviato con i placeholder grezzi.
> **D5** (condizione "lead non ha ancora risposto") risulta **ridondante** per il percorso raccomandato: la guardia è già garantita dallo scan idle (`list_reminder_candidates`, `conversation.py:122-157`). Servirebbe solo per il path event-driven (B1, scartato).

## 4. Lavoro ARCHITETTURALE — far guidare la tempistica al grafo

| # | Lavoro | Dove | Note (correzioni dal critico) |
|---|---|---|---|
| **AR1** | Nuovo resolver `resolve_send_plan(nodes, edges, ctx)` che **accumula** i `wait` (oggi saltati) e restituisce il piano `[PlannedSend(...)]`; `max_attempts = len(plan)` | `ai_core/automations.py` + `lifecycle.py` | I gap devono essere **per-step incrementali** (gli scheduler spaziano da `last_*_at`), con la **soglia iniziale trigger→send#1 distinta** dagli intervalli successivi. Non un singolo `cumulative_delay` |
| **AR2** | Riscrivere gli scheduler perché leggano soglia/cadenza/conteggio dal piano (ConfigKey come fallback) | `no_answer.py`, `reactivation.py` | **NON allargare i floor di scan** (sono già il min permissivo). Il vero tetto sono i **conteggi hardcoded**: `_scan_candidates(max_followups=4)`, `max_attempts=5`, filtro repo `reminders_sent < max_followups` (`conversation.py:157`). `validate_graph`/D6 deve **cap-pare il n. di `send`** a quei tetti (o alzarli) |
| **AR3** | Booking: leggere l'offset-ore dal grafo nell'action di prenotazione | `booking.py:288-292` → alimenta `build_reminder_schedule` (`appointment.py:19-40`) | — |
| **AR4** | Vincolo del resolver + ADR | `routers/automations.py` (`_guard_system_flow_edit`) | `resolve_send_node_at`/`evaluate_condition` sono **sync/IO-free**: un `ai_check` lì cade in fail-closed (False) **silenzioso**. `_guard_system_flow_edit` blocca solo `send_template`/`send_message`, **non** `ai_check`/`ai_reply`/`wait`/`set_lead_field`/`human_handoff` nei system flow → vanno vietati o gestiti. Condizioni async vanno calcolate dallo scheduler e iniettate nel `context` |

`engine.py` **non va toccato** su questo percorso (i system flow restano esclusi da `automation_run`).

## 5. Nota: 4° flusso di sistema `first_contact` *(scoperto dal critico)*

`SYSTEM_AUTOMATION_KEYS` ha **4** chiavi e `ensure_system_automations` ne semina 4 sul canvas, ma `resolve_lifecycle_step` è chiamato solo per i 3 di questo audit. **`first_contact` è seminato, editabile e attivabile sulla lavagnetta ma nessun codice lo consuma → inerte.** Il suo trigger `message_received` vive nell'hot path inbound (`conversation_service`), **non** in uno scheduler. Decisione richiesta: cablarlo o nasconderlo.

## 6. Opzioni di scope

- **A1 — "zucchero UI"**: al salvataggio proietto il grafo nei `bot_configs.overrides`, scheduler invariati. Rischio quasi nullo, ma mapping grafo→config **lossy** (canvas vincolato a forma lineare) e `booking_reminder` non entra comunque in un `wait`. **Non realizza davvero "i blocchi guidano il comportamento".**
- **A2 — il grafo è sorgente del timing, scheduler = executor** (D1-D4,D6 + AR1-AR4): conserva cron, scan candidati, RLS, gate 24h `decide_outbound`, dedup Redis, optimal-hour. `engine.py` intatto, **zero rischio durabilità**. **~M-L (≈2-3 settimane).** Primo target sensato.
- **B1 — unificare sul motore event-driven** (`automation_run` con `wait` differito): **sconsigliata**. Trigger temporali ≠ eventi; attese di giorni su `_defer_by` vivono solo in Redis (memoria progetto: "Redis giù = timer persi"); dedup 24h non regge catene multi-giorno.
- **B2 — scheduler 100% graph-driven, card rimosse**: A2 + migrazione `overrides→grafo` + card come "vista semplice" che round-trippa sul grafo. Mantiene cron+DB-counter+gate 24h → nessun rischio Redis. **A2 + ~M.**

**Raccomandazione:** **A2 → (poi) B2.** Evitare A1 (lossy, non risolve booking) e B1 (rischio Redis). Prerequisiti non negoziabili di qualsiasi spostamento del timing: **D1, D2, D3, D4, D6** + gli ADR di AR4.

## 7. File chiave

`backend/libs/ai_core/src/ai_core/automations.py` · `backend/workers/automation/lifecycle.py` · `backend/workers/scheduler/{no_answer,reactivation,appointment_reminder}.py` · `backend/libs/ai_core/src/ai_core/actions/booking.py` · `backend/libs/db/src/db/repositories/appointment.py` · `backend/libs/db/src/db/models/automation.py` · `backend/workers/outbound.py` (D4) · `backend/services/api/src/api/routers/automations.py` · `frontend/apps/web-merchant/src/components/automazioni/{automation-nodes.tsx,automation-editor.tsx,automazioni-panel.tsx}`. `backend/workers/automation/engine.py` toccato **solo** in B1 (da evitare).
