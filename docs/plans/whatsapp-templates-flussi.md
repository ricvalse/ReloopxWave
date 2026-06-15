# Piano — Template WhatsApp 360dialog + sezione "Flussi"

> Stato: **proposta / da approvare**. Riferimento d'intenti: `reloop-ai-architettura.md`.
> Codice di riferimento già funzionante: progetto **Amalia** (`/Users/riccardo/Progetti/Amalia/amalia-ai`).
> Questo documento pianifica il porting della logica template di Amalia in Reloop, l'integrazione nei casi d'uso e la valutazione di una sezione "Flussi".

---

## 0. TL;DR

1. **Reloop oggi invia messaggi proattivi come testo libero** (`send_text`) in UC-03 (no-answer), UC-06 (riattivazione) e UC-02 (conferma booking). Fuori dalla **finestra di 24h** di WhatsApp questo è **non conforme**: Meta accetta solo **template approvati**. Riattivazione (90 gg) e 2° reminder no-answer (24h) cadono *sempre/quasi-sempre fuori finestra* → oggi quei messaggi verrebbero rifiutati dal provider. **Questa è la motivazione primaria del lavoro: è un fix di correttezza, non solo una feature.**
2. Il `D360WhatsAppClient.send_template()` **esiste già** ma non è mai chiamato e non è esposto nel `WhatsAppSender` Protocol. Manca tutto il resto: **creazione** template, **sync stato** approvazione, **modello DB**, **API**, **UI**, e la **logica finestra-24h** che decide testo-libero vs template.
3. Porto da Amalia: client management 360dialog (`/v1/configs/templates`), linter, status-sync (webhook + cron), modello `whatsapp_templates`, builder componenti/variabili. Amalia è TS+Python: porto *forme e logica*, non il codice.
4. **Sezione "Flussi": sì, ha senso** — ma come **strato di configurazione sottile sopra gli scheduler esistenti** (no-answer/riattivazione/reminder booking resi template-bound ed editabili), **non** come journey-builder visuale generico in V1. Le campagne broadcast (stile Amalia) sono un epic separato (V2).

---

## 1. Stato attuale

### 1.1 Reloop (questo repo)

| Componente | File | Cosa fa | Invia WA? |
|---|---|---|---|
| Client 360dialog | `backend/libs/integrations/src/integrations/whatsapp/d360_client.py` | `send_text`, **`send_template` (già presente, mai usato)**, `send_interactive`; POST `waba-v2.360dialog.io/messages`, header `D360-API-KEY` | sì |
| Factory / Protocol | `…/whatsapp/factory.py` | `WhatsAppSender` Protocol espone **solo `send_text`** | — |
| Sender worker | `backend/workers/runtime.py:WhatsAppReplySender` | bridge verso `ConversationService` | sì (text) |
| UC-01 reply | `backend/libs/ai_core/src/ai_core/conversation_service.py:~395` | risposta LLM nel thread | sì (text) |
| UC-02 booking | `backend/libs/ai_core/src/ai_core/actions/booking.py:~340` | conferma/alternative slot (stringhe hardcoded) | sì (text) |
| UC-03 no-answer | `backend/workers/scheduler/no_answer.py:140` | reminder (stringhe `REMINDER_TEXTS` o override config) | sì (text) |
| UC-06 riattivazione | `backend/workers/scheduler/reactivation.py:144` | riattivazione dormienti (stringhe `REACTIVATION_TEXTS`) | sì (text) |
| Webhook parsing | `…/whatsapp/webhook.py` | parse inbound / status / echo (Coexistence) | — |
| Integration repo | `backend/libs/db/src/db/repositories/integration.py:resolve_whatsapp` | ritorna `phone_number_id` + `api_key` (decrypt AES-GCM) + `waba_base_url` | — |
| Config schema | `backend/libs/config_resolver/src/config_resolver/schema.py` | `ConfigKey` enum + `SYSTEM_DEFAULTS` + modelli Pydantic per-sezione | — |

**Mancano del tutto:** logica finestra-24h, modello DB template, creazione template, sync stato approvazione, linter, API/UI template, config-key per nomi-template.

> ⚠️ Nota di disambiguazione: il route `web-admin/(app)/templates` è **UC-10 "bot prompt templates"** (default di configurazione del bot), **non** template WhatsApp. Vanno tenuti distinti anche nel naming UI (vedi §8).

### 1.2 Amalia (riferimento funzionante)

| Componente | File Amalia |
|---|---|
| Submit template | `packages/whatsapp/provider/dialog360-templates.ts` → `POST waba-v2.360dialog.io/v1/configs/templates` |
| Builder componenti | `packages/whatsapp/provider/template-builders.ts` (HEADER/BODY/FOOTER/BUTTONS, `{{n}}`, example values, nome unico con suffisso base36) |
| Linter | `packages/whatsapp/lib/template-linter.ts` (regole variabili, lunghezze, bottoni) |
| Status webhook | `apps/web/app/api/webhooks/360dialog/template-status/route.ts` |
| Status cron poll | `apps/web/app/api/cron/template-status-sync/route.ts` → `GET /v1/configs/templates?name=…` |
| Modelli | `Template`, `Campaign`, `CampaignRecipient`, `CampaignClick` (Prisma) |
| Send template (py) | `services/backend/app/services/campaigns/sender.py`, `template_components.py`, `variable_bindings.py` |

Mappatura stato Meta→locale: `APPROVED→approved`, `REJECTED|DISABLED→rejected`, `PAUSED|FLAGGED|PENDING→pending_approval`.
Amalia **non ha** un modello "Flow": usa slot-template per tipo-automazione sullo `Store` + campagne bulk.

---

## 2. Architettura target (3 strati)

```
┌─────────────────────────────────────────────────────────────────┐
│ STRATO 3 — FLUSSI (configurazione/orchestrazione)                 │
│  flows + flow_steps: per ogni step → {trigger, delay, template,   │
│  variable mapping, window-policy}. UI merchant "Flussi".          │
│  Gli scheduler esistenti diventano esecutori di step.             │
├─────────────────────────────────────────────────────────────────┤
│ STRATO 2 — BINDING NEI CASI D'USO + DECISIONE FINESTRA-24h        │
│  resolve_outbound(): dentro 24h → testo libero; fuori → template  │
│  approvato. UC-03 / UC-06 / UC-02-reminder / UC-01-first-message. │
├─────────────────────────────────────────────────────────────────┤
│ STRATO 1 — MOTORE TEMPLATE (infrastruttura)                       │
│  modello whatsapp_templates · D360 management client (create/     │
│  sync/delete) · component builder · linter · status webhook+cron  │
│  · send_template esposto nel Protocol · API CRUD · UI editor      │
└─────────────────────────────────────────────────────────────────┘
```

Principio guida: **Strato 1 è obbligatorio** (è l'infrastruttura). **Strato 2 è il fix di compliance** e va fatto subito dopo. **Strato 3 (Flussi)** è il packaging UX/prodotto e può essere V1-light o V2 (vedi §6).

---

## 3. Strato 1 — Motore template

### 3.1 Modello DB `whatsapp_templates` (nuova migration `0014_whatsapp_templates`)

Merchant-scoped, con RLS come le altre tabelle merchant (`merchant_isolation_whatsapp_templates`, predicato EXISTS via `merchants.tenant_id`, vedi `0001_initial.py:758`).

Campi (porting ridotto del `Template` Amalia, senza la parte campagne):

```
id                uuid pk
merchant_id       uuid fk merchants(id) on delete cascade  [RLS]
name              text         # nome 360dialog, univoco per WABA (suffisso base36)
category          text         # MARKETING | UTILITY | AUTHENTICATION
language          text default 'it'
purpose           text         # tag funzionale: no_answer_1 | no_answer_2 |
                               # reactivation | booking_reminder | first_contact | custom
status            text default 'draft'   # draft|pending_approval|approved|rejected
# corpo & componenti
header_type       text default 'NONE'    # NONE|TEXT|IMAGE
header_text       text null
header_image_url  text null
body              text                   # con placeholder {{1}}..{{n}}
footer            text null              # max 60
buttons           jsonb null            # [{type,text,url?,...}]
variables         text[]                # ['1','2',...] estratte dal body
variable_sources  jsonb null            # {"1":"lead.first_name","2":"booking.slot"}
# sync 360dialog
whatsapp_template_id  text null         # id Meta
meta_status       text null             # PENDING|APPROVED|REJECTED|PAUSED|DISABLED
meta_quality      text null            # HIGH|MEDIUM|LOW
rejection_reason  text null
submitted_at / approved_at / rejected_at / meta_last_synced_at  timestamptz null
created_at / updated_at  timestamptz
```

Indici: `(merchant_id, purpose)`, `(merchant_id, status)`, unique `(merchant_id, name)`.

> Decisione: **niente edit-lineage/`replaces` in V1** (la macchina supersede di Amalia è complessa). In V1 l'editing di un template approvato crea un nuovo record e il merchant ri-seleziona; lineage va in backlog.

### 3.2 Client management 360dialog

Nuovo file `backend/libs/integrations/src/integrations/whatsapp/d360_templates.py` (o estendere `d360_client.py`):

- `create_template(*, name, category, language, components) -> dict` → `POST {base}/v1/configs/templates` con header `D360-API-KEY` (stesso key/base di `resolve_whatsapp`).
- `fetch_template_status(*, name) -> dict` → `GET {base}/v1/configs/templates?name=…` (campi `waba_templates[].status/quality_score/category/rejected_reason/id`).
- `delete_template(*, name)` (opzionale V1).
- Riuso del retry/tenacity e dell'`IntegrationError` già presenti nel client.

### 3.3 Esporre `send_template` nel Protocol

`factory.py`: estendere `WhatsAppSender` Protocol con `send_template(...)` (la implementazione esiste già in `D360WhatsAppClient`). Aggiungere a `WhatsAppReplySender.send` (in `runtime.py`) un metodo gemello `send_template(...)` per i casi d'uso.

### 3.4 Component builder + linter (porting da Amalia)

`backend/libs/ai_core/src/ai_core/whatsapp/templates.py` (o in `integrations`):
- `build_components(body, examples, footer, buttons, header)` → lista componenti per il **submit**.
- `build_send_components(variable_values: dict[str,str])` → `[{"type":"body","parameters":[{"type":"text","text":...}]}]` per il **send**.
- `lint_template(...)` → porting di `template-linter.ts`: variabili `{{1..n}}` sequenziali senza buchi, max 10, non isolate/inizio/fine riga; header/footer ≤60 e senza variabili; bottoni (max 3 quick-reply *oppure* max 2 URL; var URL solo trailing).

### 3.5 Sync stato approvazione

Due percorsi, come Amalia:
1. **Webhook** — il webhook 360dialog può recapitare eventi template (`change.value.event = APPROVED|REJECTED|…`). Aggiungere a `webhook.py` un `parse_template_status_payload()` e gestirlo nel router webhook (`services/api/src/api/routers/webhooks.py`) → aggiorna `meta_status`/`status`/`rejection_reason`. **HMAC come gli altri webhook** (invariante di sicurezza).
2. **Cron fallback** — nuovo job `template_status_sync` in `backend/workers/scheduler/` registrato in `workers/settings.py:WorkerSettings.cron_jobs` (es. ogni ora): per ogni template `pending_approval`, `fetch_template_status` e aggiorna.

### 3.6 API CRUD

Nuovo router `services/api/src/api/routers/whatsapp_templates.py`:
- `GET /whatsapp-templates` (lista per merchant; filtri `purpose`,`status`).
- `POST /whatsapp-templates` (lint → genera nome univoco → `create_template` → persist `pending_approval`).
- `GET /whatsapp-templates/{id}` ; `DELETE` (opz).
- `POST /whatsapp-templates/{id}/sync` (force-sync manuale).

RBAC: gestione template a livello **merchant** (e agency per i default — vedi §6). Dopo la modifica firma OpenAPI → rigenerare il client TS (`scripts/generate-api-types.sh`).

---

## 4. Strato 2 — Decisione finestra-24h (il cuore della compliance)

### 4.1 Regola

WhatsApp consente messaggi **free-form** solo entro **24h dall'ultimo messaggio inbound del cliente**. Fuori finestra → **solo template approvati** (qualunque categoria). La risposta del cliente a un template riapre una nuova finestra 24h.

### 4.2 Cosa serve in Reloop

- Un timestamp affidabile **`last_inbound_at`** per conversazione (ultimo messaggio del *cliente*). Verificare se `ConversationRepository.list_reminder_candidates` espone già un campo inbound; altrimenti aggiungerlo alla query/candidate.
- Un helper centrale:

```python
def is_within_24h(last_inbound_at: datetime, now: datetime) -> bool:
    return last_inbound_at is not None and (now - last_inbound_at) < timedelta(hours=24)
```

- Un dispatcher unico `send_outbound(..., template_ref, free_text)`:
  - dentro finestra → `send_text(free_text)` (comportamento attuale);
  - fuori finestra → se esiste template **approvato** per quello step → `send_template(...)`, altrimenti **skip + analytics `outbound.skipped_no_template`** (mai inviare free-form fuori finestra).

### 4.3 Matrice per caso d'uso

| Caso d'uso | Trigger tipico | Posizione vs 24h | Strategia |
|---|---|---|---|
| **UC-03 no-answer #1** | +120 min idle | di norma **dentro** | testo libero (attuale) con fallback template se fuori |
| **UC-03 no-answer #2** | +1440 min (24h) | **sul bordo / fuori** | **template obbligatorio** (UTILITY) |
| **UC-06 riattivazione** | 90 gg dormiente | **sempre fuori** | **template obbligatorio** (MARKETING/UTILITY) |
| **UC-02 conferma booking** | subito post-azione | **dentro** | testo libero (attuale) ok |
| **UC-02 reminder appuntamento** *(nuovo)* | 24h prima slot | **fuori** | **template obbligatorio** (UTILITY) |
| **UC-01 first contact** | merchant→nuovo lead | **fuori** (nessuna inbound) | **template obbligatorio**; oggi `bot.first_message` è inutilizzato |
| UC-01 reply in-thread | risposta a inbound | **dentro** | testo libero LLM (attuale) ok |

### 4.4 Modifiche puntuali

- `no_answer.py`: sostituire la scelta `REMINDER_TEXTS/override` con `send_outbound`; per #2 usare template.
- `reactivation.py`: sempre `send_template`.
- `booking.py`: confermare resta free-text; aggiungere (nuovo) job reminder appuntamento template-bound (oggi il commento "Ti invieremo il promemoria" non ha implementazione).
- `conversation_service.py` / nuovo job: cablare `bot.first_message` come template di primo contatto (oggi config-key esiste ma è morta).

---

## 5. Config schema (`schema.py`)

Aggiungere `ConfigKey` + default + modello Pydantic per il binding step→template. Opzioni di binding:

```
# Per-step: nome template approvato + lingua (le variabili sono mappate dal flow/step)
no_answer.first_reminder_template     (str|None)
no_answer.second_reminder_template    (str|None)
reactivation.template                 (str|None)
booking.reminder_template             (str|None)
booking.reminder_hours_before         (int, default 24)
bot.first_contact_template            (str|None)
```

Se si adotta lo Strato 3 (Flussi), questi binding **migrano dentro `flow_steps`** e i config-key restano solo come fallback/retro-compatibilità. Ricordarsi: ogni nuovo `ConfigKey` richiede default in `SYSTEM_DEFAULTS` + codegen OpenAPI.

---

## 6. Strato 3 — "Flussi": ha senso? (raccomandazione)

**Sì, ma con scope V1 contenuto.** Reloop *ha già* dei flussi, solo che sono **hardcoded negli scheduler** (no-answer = step1@120m → step2@24h; riattivazione = step1@90gg → step2/3@7gg). Introdurre i template senza un contenitore lascerebbe i binding sparsi tra config-key. Una sezione "Flussi" dà il posto naturale dove i template vengono *usati* e unifica il modello mentale.

Due interpretazioni:

- **(A) Flussi = sequenze outbound configurabili** (CONSIGLIATA per V1). Modello `flows` + `flow_steps`; ogni step = `{trigger_type, delay, template_id, variable_mapping, window_policy}`. Gli scheduler esistenti (no-answer, riattivazione, reminder booking, first-contact) diventano **esecutori** che leggono gli step invece delle costanti. Rischio **basso**, coerenza **alta**, abilita nuovi flussi senza codice. UI merchant: nuova voce **"Flussi"** (o "Automazioni") con, per ogni stage del ciclo-vita, lo step e il template approvato collegato.
- **(B) Flussi = campagne/broadcast** (stile Amalia `Campaign`): audience/segmento + template MARKETING + schedule → invio bulk con snapshot destinatari, throttling, click-tracking. **Valore marketing alto ma epic molto più grande** (audience builder, recipient snapshot, rate-limit, click redirector). → **V2 separato.**

**Raccomandazione: (A) in V1, (B) in backlog V2.** (A) è il giusto livello di astrazione da introdurre insieme ai template; (B) è un prodotto a sé.

Modello (A) minimale:

```
flows
  id, merchant_id [RLS], key (no_answer|reactivation|booking_reminder|first_contact|custom),
  name, enabled bool, created_at/updated_at
flow_steps
  id, flow_id fk, step_index int, delay_minutes int,
  template_id fk whatsapp_templates(id) null,   # null = usa testo libero se in finestra
  variable_mapping jsonb,                         # {"1":"lead.first_name",...}
  window_policy text  # auto | require_template | freeform_only
```

> Nota: questo è anche allineato alla cascata config a 3 livelli — i flussi possono avere **default agency** e **override merchant**, esattamente come `bot_templates`/`bot_configs`. Valutare se modellare i flussi come parte degli override JSONB (coerente con l'attuale architettura) invece di tabelle dedicate, per non duplicare il meccanismo di cascade. **Decisione aperta (D3).**

---

## 7. Frontend

- **Editor template WhatsApp** (merchant): nuova route `web-merchant/(app)/bot/whatsapp-templates/` o sotto `integrations/`. Form: categoria, lingua, header/body/footer/buttons, anteprima, **lint live** (porting regole), submit → stato `pending_approval`, badge stato (con polling/Realtime). Componenti shadcn da `packages/ui`.
- **Sezione "Flussi"** (se Strato 3-A): `web-merchant/(app)/flussi/` — lista flussi del ciclo-vita, per ogni step selezione del template approvato + mapping variabili + toggle finestra. NON un canvas drag&drop in V1 (lista di step).
- **Agency (web-admin):** opzionale, default di flusso/template a livello agency (cascade). Tenere il naming distinto da UC-10: "Template Bot" (UC-10, prompt) vs **"Template WhatsApp"** (questo lavoro).
- Dopo ogni cambio firma API → `scripts/generate-api-types.sh` e commit di `generated.ts` (CI fallisce su drift).

---

## 8. Sicurezza & compliance (invarianti da non regredire)

- Nuove tabelle `whatsapp_templates` (+ eventuali `flows`/`flow_steps`) → **RLS** su `merchant_id` (e tenant via join), con isolation test in `tests/integration/test_isolation*.py`.
- Webhook template-status → **HMAC-SHA256** come gli altri webhook; droppare eventi non firmati.
- La D360-API-Key resta **cifrata AES-256-GCM** in `integrations`; il management client la legge via `IntegrationRepository` (mai in chiaro nei log).
- **Mai** inviare free-form fuori dalla finestra 24h (regola Meta) — il dispatcher §4.2 è il punto di enforcement.
- Categoria template corretta (UTILITY vs MARKETING) per evitare blocchi/qualità bassa.

---

## 9. Piano a fasi (tabella stato — aggiornare man mano, stile `completion-plan.md`)

| # | Fase | Task | Dipende da | Stato |
|---|---|---|---|---|
| 1.1 | Motore | Migration `0014_whatsapp_templates_flows` + modelli + RLS + isolation test | — | ✅ |
| 1.2 | Motore | `d360_templates.py` (create/fetch_status) + estendere `WhatsAppSender` Protocol con `send_template` | — | ✅ |
| 1.3 | Motore | Component builder + linter (porting Amalia) | 1.2 | ✅ |
| 1.4 | Motore | Status sync: `parse_template_status_payload` + handler webhook (HMAC) + cron `template_status_sync` | 1.1 | ✅ |
| 1.5 | Motore | API router `whatsapp_templates` (CRUD+sync) + codegen OpenAPI | 1.1–1.3 | ✅ |
| 1.6 | Motore | UI editor template WhatsApp (merchant) con lint hint + stato | 1.5 | ✅ |
| 2.1 | Compliance | Helper `is_within_24h` + `last_inbound_at` su conversation + dispatcher `decide_outbound` | 1.2 | ✅ |
| 2.2 | Compliance | UC-06 riattivazione → template (sempre fuori finestra) | 2.1 | ✅ |
| 2.3 | Compliance | UC-03 no-answer #2 → template; #1 window-aware | 2.1 | ✅ |
| 2.4 | Compliance | UC-02 reminder appuntamento (nuovo job) → template | 2.1 | ⏳ *(flow key `booking_reminder` + dispatcher pronti; manca il job trigger — serve sorgente appuntamenti GHL)* |
| 2.5 | Compliance | UC-01 first-contact → cablare `bot.first_message`/template | 2.1 | ⏳ *(flow key `first_contact` pronto; manca il trigger di outreach merchant-initiated)* |
| 3.1 | Flussi | Modello `flows`/`flow_steps` + repository | 2.x | ✅ *(tabelle dedicate — vedi D3)* |
| 3.2 | Flussi | Scheduler diventano esecutori di step (`FlowRepository.resolve_step`) | 3.1 | ✅ |
| 3.3 | Flussi | UI "Flussi" (merchant) | 3.1, 1.6 | ✅ |
| V2 | Campagne | Broadcast/audience/click-tracking (epic separato) | tutto sopra | ☐ |

> **Stato implementazione (2026-06-14):** Strato 1 (motore) + Strato 2 (compliance no-answer/riattivazione) + Strato 3-A (Flussi) completi e verificati end-to-end. Backend: `ruff check`/`ruff format`/`mypy --strict` puliti, 154 unit test verdi (3 nuovi file), isolation test RLS aggiunti per `whatsapp_templates`/`flows`/`flow_steps` (auto-skip senza DB). Frontend: client OpenAPI rigenerato, `typecheck`/`lint`/`build` web-merchant verdi (route `/whatsapp-templates` e `/flussi`). Migration alembic: head lineare `0013 → 0014`.
>
> **Rimandato (2.4 / 2.5):** i due flow key esistono e il dispatcher li gestisce, ma i *trigger job* non sono stati creati perché richiedono fonti dati non ancora presenti: il reminder appuntamento ha bisogno di leggere gli appuntamenti imminenti da GHL (nessuna tabella appuntamenti locale); il first-contact ha bisogno di un trigger di outreach merchant-initiated (oggi inesistente). Il modello dati è pronto per entrambi.

### Decisioni risolte in implementazione
- **D2 (chi gestisce i template):** V1 = **solo merchant** (route merchant-scoped; default agency rimandati).
- **D3 (modellazione Flussi):** **tabelle dedicate** `flows`/`flow_steps` (non override JSONB) — più semplici da interrogare per gli scheduler e con RLS standard.
- **D4 (edit-lineage):** confermato **rimandato** — l'editing ricrea + ri-seleziona.
- **Config-key per binding template:** **non aggiunti** — i Flussi tengono i binding, evitando duplicazione con la cascade.

ADR da scrivere: `docs/decisions/0006-whatsapp-templates-and-flows.md` (tabelle-dedicate per i flussi; no edit-lineage in V1; enforcement finestra-24h nel dispatcher `decide_outbound`).

---

## 10. Decisioni

- **D1 — Scope V1 dei Flussi: ✅ DECISO → (A) sequenze configurabili** sopra gli scheduler esistenti (no-answer, riattivazione, reminder booking, first-contact resi editabili e template-bound; lista di step, niente canvas drag&drop). Le campagne broadcast (B) restano epic V2. → si procede con le fasi 3.1–3.3.

### Aperte

- **D2 — Chi gestisce i template:** solo merchant, o anche default a livello agency (cascade)?
- **D3 — Modellazione Flussi:** tabelle dedicate `flows`/`flow_steps` *vs* estensione degli override JSONB esistenti (coerente con `bot_configs`/`bot_templates` e la cascade a 3 livelli).
- **D4 — Edit-lineage template:** confermare il rinvio a backlog (V1: ri-creazione + ri-selezione).
