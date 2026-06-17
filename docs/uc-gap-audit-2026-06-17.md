# Reloop AI — Report di Chiusura V1: Gap, Funzioni da Aggiungere, Sequenza

> Generato il 2026-06-17 da uno sciame di 117 agenti (19 audit + verifica avversariale per ogni gap + sintesi). Dati strutturati in `uc-gap-audit-2026-06-17.gaps.json` (92 gap confermati: 85 reali + 7 parziali; 4 blocker, 43 major, 45 minor).

Audit verificato avversarialmente. 19 schede (13 UC + 6 trasversali), tutti `partial`. 92 gap confermati (78 dopo accorpamento dei doppioni cross-UC). Nessun UC è `complete`: ognuno ha un percorso core funzionante ma manca pezzi del capitolato.

---

## 1. Verdetto sintetico

### I 13 Use Case del capitolato

| UC | Stato reale | #gap | Nota |
|----|-------------|------|------|
| UC-01 First Response | partial | 5 | Core ok, ma nome/email mai persistiti + auto_reply OFF di default |
| UC-02 Booking | partial | 6 | Prenota ok ma nessun reminder, slot arbitrari, errore GHL = "slot occupato" |
| UC-03 Senza Risposta | partial | 3 | **Blocker**: reinterpretato come silenzio-chat, manca trigger "chiamata fallita" |
| UC-04 Pipeline | partial | 5 | Move opportunity ok; metà UC (note+sentiment su GHL) totalmente assente |
| UC-05 Lead Scoring | partial | 2 | Cumulativo solo per behavioural; content signals crollano + bug type non validato |
| UC-06 Riattivazione | partial | 6 | Nessun opt-out/STOP, no filtro stato lead, ignora takeover umano, zero test |
| UC-07 Knowledge Base | partial | 5 | Manca delete doc, reindex/URL non in UI, no test retriever |
| UC-08 Playground | partial | 4 | Test bot ok; mancano "regole" e "salva config" del capitolato |
| UC-09 A/B Testing | partial | 5 | **Blocker**: eventi conversione senza variant_id → metriche sempre 0 |
| UC-10 Bot Default | partial | 5 | Editor JSON grezzo, schema non extra=forbid (silent drop), no delete |
| UC-11 Dashboard Merchant | partial | 5 | Manca filtro campagna+periodo UI, leak RLS cross-merchant, denominatori incoerenti |
| UC-12 Dashboard Agency | partial | 5 | Ranking non cliccabile, rollup morto, export non in UI, conversion mismatch |
| UC-13 Obiezioni | partial | 6 | No filtro bot, no vista agenzia, categorie hardcoded, zero test |

### I 6 trasversali (CC-*)

| CC | Stato reale | #gap | Nota |
|----|-------------|------|------|
| CC-FT Fine-tuning | partial | 6 | **Blocker**: presidio italiano misconfigurato aborta ogni export; A/B rollout codice morto |
| CC-TENANCY RLS | partial | 5 | **Blocker**: tenant_session senza SET ROLE → RLS bypassata se conn=owner |
| CC-GHL Integrazione | partial | 6 | Manca note/custom-fields (contrattuale), event no-op, health morto |
| CC-WA WhatsApp | partial | 5 | Composer umano ignora finestra 24h → failed silenzioso |
| CC-CONFIG Cascade | partial | 2 | active_hours/off_hours inerti, ab_split non validato |
| CC-WIRE Frontend/OpenAPI | partial | 6 | `as never` su 124 call-site annulla type-safety; 4 endpoint non cablati |

---

## 2. Blocker (da fare subito)

Ordinati per impatto. 4 gap `blocker` confermati.

### B1 — CC-TENANCY: RLS bypassabile lato backend `[M]`
**`backend/libs/db/src/db/session.py:86` — `tenant_session()`**
- Aggiungere, subito dopo l'apertura sessione e PRIMA del `set_config`: `await session.execute(text("SET LOCAL ROLE authenticated"))` (o ruolo dedicato `app_tenant` NOSUPERUSER/NOBYPASSRLS creato in migrazione + GRANT sulle tabelle di dominio).
- **Perché blocca**: in prod ci si connette come owner `postgres.<ref>`; FORCE RLS non si applica a ruoli BYPASSRLS. Verificato empiricamente: 2/2 righe visibili sotto superuser, 1/2 sotto ruolo non-priv. I repository (`TenantRepository.list_visible` = bare `select(Tenant)`) si affidano al 100% alla RLS → **leak cross-tenant dell'intera piattaforma**.
- Dipendenza: abilita B-test fix CC-TENANCY (test di isolamento sotto ruolo applicativo).

### B2 — CC-FT: presidio italiano aborta ogni export `[S]`
**`backend/libs/ai_core/src/ai_core/ft/presidio.py:70` — `build_presidio_transform`**
- `build_italian_nlp_engine() -> NlpEngine` via `NlpEngineProvider(nlp_configuration={'nlp_engine_name':'spacy','models':[{'lang_code':'it','model_name':'it_core_news_lg'}]}).create_engine()`, passarlo a `AnalyzerEngine(nlp_engine=..., supported_languages=['it'])`.
- **Perché blocca**: `AnalyzerEngine()` istanziato senza NlpEngineProvider → carica default `en`-only. Il probe `analyze(language='it')` solleva sempre; in prod `require=True` (export.py:53) → DomainError → **`export_training_pairs` fallisce, pipeline FT mai eseguibile**. Installare `it_core_news_lg` da solo NON basta.

### B3 — UC-09: metriche A/B sempre a zero sul KPI primario `[S]`
**`backend/libs/ai_core/src/ai_core/conversation_service.py:106,887` + actions**
- Aggiungere `variant_id: str | None = None` a `TurnContext` (conversation_service.py:106); valorizzarlo a riga 887 con `variant_id=rc.conv_variant_id`; passare `variant_id=turn_ctx.variant_id` nelle 3 `analytics.emit` in `actions/booking.py:170`, `actions/scoring.py:94`, `actions/pipeline.py:156`.
- **Perché blocca**: `booking.created`/`lead_score_changed`/`pipeline.moved` escono con `variant_id=None`; `ABRepository.metrics` filtra `variant_id.in_(...)` → 0 conversioni per ogni variante. `primary_metric='booking.created'` di default → **l'esperimento A/B non può mai dichiarare un vincitore**. Tutti i prerequisiti (`rc.conv_variant_id`, param `emit`) esistono già.

### B4 — UC-03: nessun trigger su esito chiamata fallita `[M]`
**`backend/workers/conversation/handlers.py:270` — nuovo `handle_call_outcome` + routing in `handle_ghl_event`**
- `async def handle_call_outcome(ctx, *, merchant_id, contact_phone, outcome, ghl_contact_id) -> dict`: per outcome in {no_answer,busy,failed,voicemail} crea/riusa Conversation attiva, marca `meta['origin']='call_failed'`, accoda primo reminder via `FLOW_NO_ANSWER` step 0. Mappare in `handle_ghl_event` l'evento GHL di call-log a questo handler.
- **Perché blocca**: il capitolato (`capitolato-tecnico.md:51`) dice "l'AI prende il controllo quando la CHIAMATA non va a buon fine". Oggi l'unico trigger è il silenzio-chat (richiede `last_message_at`). **Un lead chiamato-e-non-raggiunto senza WhatsApp non è MAI candidato → il core di UC-03 è assente.**

---

## 3. Major

Accorpati dove condividono la fix. Severity=major.

| ID | UC | Fix concreta (file:simbolo) | Effort |
|----|----|-----------------------------|--------|
| **G-CONTACT** | UC-01, UC-04 | `LeadRepository.update_contact_fields(lead_id, *, name, email)` fill-only + `CaptureContactHandler` o scrittura in `BookSlot`/`MovePipeline`. **Sblocca scoring has_name/has_email e fallback contact_fields** | M |
| **G-GHL-EVENT** | UC-01, UC-02, UC-03, CC-GHL | Implementare routing in `handle_ghl_event` (handlers.py:270): `_route_ghl_event` per ContactUpdate/OpportunityStatusUpdate/AppointmentUpdate + `LeadRepository.set_pipeline_stage`. **Un'unica fix per 4 UC** | M |
| **G-GHL-NOTE** | UC-04, CC-GHL | `GHLClient.add_contact_note(contact_id, *, body)` (POST /contacts/{id}/notes) + cablaggio in `MovePipelineHandler._execute` + passare sentiment/collected a `TurnContext` | M |
| **G-GHL-CUSTOMFIELD** | UC-04, CC-GHL | `upsert_contact`/`create_opportunity` accettano `customFields=[{id,value}]` + ConfigKey `ghl.custom_field_map` (JSONB) | M |
| ft-deploy-no-ab | CC-FT | `merchant_id` su `RunFineTuneIn`→`fine_tune_run`→`fine_tune_train`→`FTModel(...)` (handlers.py:100). Attiva il ramo A/B di `fine_tune_deploy` | M |
| ft-eval-trainset | CC-FT | `export_training_pairs` split 90/10 → `ExportResult(path, holdout_path)`, propagare `test_set_path` fino a `fine_tune_evaluate` | M |
| ft-no-cron | CC-FT | `schedule_fine_tune_runs(ctx)` in cron_jobs (settimanale), itera tenant attivi | S |
| uc02-reminder | UC-02 | `workers/scheduler/appointment_reminder.py:send_appointment_reminders` + persistere booking_id/slot_start (tabella `bookings` o lead.meta) + cron | L |
| uc02-proactive-slots | UC-02 | ActionKind `propose_slots` + `ProposeSlotsHandler` (get_free_slots prima di prenotare) | M |
| uc02-slot-taken | UC-02 | `_is_slot_conflict(e)` su `e.context['status']` (409/422) in booking.py:255; altri errori → "ti ricontatteremo" | S |
| uc02-calendar-picker | UC-02 | `GHLClient.list_calendars(location_id)` + `GET /integrations/ghl/{merchant_id}/calendars` + `<select>` UI | S |
| uc05-content-signals | UC-05 | `LeadRepository.merge_content_signals(lead_id, new)` OR-booleano in lead.meta, chiamato in `UpdateScoreHandler` prima di score_lead | M |
| uc05-bool-validation | UC-05 | `if not isinstance(signals, dict): return {}` in `derive_signals_from_llm_payload` (scoring.py:44) | S |
| uc06-optout | UC-06 | `LeadRepository.is_opted_out`/`mark_opted_out` + colonna `leads.opted_out_at`+RLS + intercept STOP/CANCELLA in `handle_inbound_persist` + filtro in `_maybe_send` | M |
| uc06-status-filter | UC-06 | Filtro `Lead.status.notin_([...])` in `list_reactivation_candidates` **+ scrivere status terminale dopo conversione** (oggi resta sempre 'new') | M |
| uc06-tests | UC-06 | `test_uc06_reactivation.py` (soglia/interval/max/dedup/skip-no-template) | M |
| uc07-delete | UC-07 | `KnowledgeBaseRepository.delete_doc` + `DELETE /docs/{doc_id}` + bottone UI (CASCADE elimina chunk) | M |
| uc08-rules | UC-08 | `override_rules` su `PlaygroundTurnIn` + `apply_playground_rule_overrides(prompt, rules)` + pannello UI | M |
| uc08-save | UC-08 | `POST /playground/apply` o riuso `PUT /bot-config/overrides` + bottone "Salva" | M |
| uc09-stop | UC-09 | `POST /{id}/stop` + `ABRepository.stop(id, winner)` (status='completed', ended_at) + bottone UI | S |
| uc09-e2e-test | UC-09 | `test_ab_metrics_attributes_conversion_to_variant` | M |
| uc10-field-editor | UC-10 | Estrarre `FieldRow`/`FieldInput` da bot-config-panel in `packages/ui` + riuso in `TemplatesPanel` | M |
| uc10-extra-forbid | UC-10 | `model_config = ConfigDict(extra='forbid')` su `BotConfigSchema` + tutti i sub-model (schema.py) | S |
| uc11-campaign | UC-11 | Colonna `Lead.campaign` + migration + propagare in events + param `campaign` in `merchant_kpis` + `GET /analytics/merchant/campaigns` + dropdown | L |
| uc11-period-ui | UC-11 | State `sinceDays` + `<Select>` in merchant-dashboard.tsx (backend già pronto) | S |
| **G-ANALYTICS-RLS** | UC-11, CC-TENANCY | Migrazione 0017: policy tenant-OR-merchant su `analytics_events` E `ft_models` (predicato come `users`) + test isolamento intra-tenant | S |
| uc12-conversion-mismatch | UC-12 | `.where(Lead.created_at >= since)` in `merchants_ranking.totals_stmt` e `tenant_totals.leads_total` | S |
| uc12-tests | UC-12 | `test_uc12_agency_dashboard.py` (ranking sort, somma per tenant, RLS due tenant) | M |
| uc13-bot-filter | UC-13 | Colonna `Objection.bot_variant`/`ab_variant_id` + migration + param in repo/endpoint | M |
| uc13-agency-view | UC-13 | `GET /reports/objections/agency` (ctx.merchant_id None) + `category_histogram_tenant` + pagina web-admin | M |
| uc13-tests | UC-13 | `test_uc13_objections.py` (classify guardrail, idempotenza, trend, fan-out) | M |
| **G-COMPOSER-24H** | CC-WA | `send_outbound_whatsapp` (handlers.py:549): leggere `conv.last_inbound_at`, gate `is_within_24h`; fuori finestra → failed con `outside_24h_window` o `send_decision` template | M |
| ccwa-composer-ui | CC-WA | Esporre `last_inbound_at` (OpenAPI+types.ts+select), banner "finestra chiusa" + selettore template in composer.tsx | M |
| ccwa-template-send | CC-WA | `POST /conversations/{id}/messages` con `{template_name,language,variables}` + ramo `meta.kind=='template'` in send_outbound_whatsapp | M |
| ccwire-as-never | CC-WIRE | Rimuovere `as never` (124 occorrenze, 28 file), tipare risposte con `components['schemas']` + eslint rule `no-restricted-syntax` | M |
| ccwire-export-ui | CC-WIRE, UC-12 | Bottone "Esporta CSV" → `POST /analytics/exports` + polling download in agency/merchant dashboard | M |
| cctenancy-isolation-tests | CC-TENANCY | Ruolo `app_test` NOSUPERUSER + `SET LOCAL ROLE` nel conftest + assert `rolbypassrls=false` | M |
| cfg-active-hours | CC-CONFIG | `is_within_active_hours(active_hours, tz, now)` + gate inizio turno in conversation_service, invia `SCHEDULE_OFF_HOURS_MESSAGE` | M |

---

## 4. Minor (compatto)

| UC | Fix | Effort |
|----|-----|--------|
| UC-01 | Default `bot.auto_reply_enabled=true` nel template di sistema/onboarding (o override a creazione merchant) | S |
| UC-01 | STT/OCR media: `download_media` + `transcribe_audio`/`ocr_image` (preservare media_id nel parser) | L |
| UC-01 | `test_uc01_inbound.py`: solo scenari gate auto_reply OFF + media placeholder (debounce e signals già coperti) | S |
| UC-02 | Risolvere `BOOKING_LOOKAHEAD_DAYS` in `_try_book` (oggi window hardcoded 3gg) | S |
| UC-03 | Esporre `no_answer.first/second_reminder_text` in bot-config-panel.tsx (kind 'text') | S |
| UC-03 | Limitare `max_followups` a 2 OPPURE testi flow per attempt 3/4 | S |
| UC-06 | Mostrare sezione `reactivation` (già in SECTIONS, in HIDDEN_SECTIONS) + interpolazione `{name}` nei testi | S/M |
| UC-07 | UI doc da URL, reindex manuale (endpoint esistono), `status_detail` per failed | S |
| UC-07 | `test_uc07_retriever.py` / `test_uc07_extractor.py` | S |
| UC-08 | `test_playground_router.py` (gate merchant_context, mapping) | S |
| UC-09 | `two_proportion_ztest` + `p_value`/`significant`/`winner` in metrics; `.order_by(started_at)` + guard single-running | S |
| UC-10 | `DELETE /templates/{id}` + `BotTemplateRepository.delete`; decidere `BotConfig.template_id` (cablare o rimuovere); test strip locked_keys | S |
| UC-11 | `Lead.created_at >= since` per booking_rate/score_distribution; `test_uc11_analytics.py` | S/M |
| UC-12 | Ranking `<tr>` cliccabile → `/merchants/{id}`; cablare/rimuovere `daily_kpi_rollup` (+ soglia hot da config) | S |
| UC-13 | `objections.categories` in config + risoluzione in `extract_for_conversation`; selettore periodo UI; soglia `IDLE_CLOSE_MINUTES` da config | S/M |
| CC-FT | `test_ft_routing.py` per `ModelRouter.select`+`FtModelResolver.get` (should_use_ft già coperto) | S |
| CC-TENANCY | Filtro esplicito tenant/merchant in `TenantRepository.list_visible/get`; marker `actor='system:worker'` nei log service-role worker | S |
| CC-GHL | Refresh proattivo token in `_request` (expires_at - 120s); `test_ghl_event.py`; health-check su `ghl_location_tokens` | S/M |
| CC-WA | `test_webhook_signature.py` (401 prima dell'enqueue); `test_outbound_composer.py` | S |
| CC-CONFIG | `field_validator` su `ab_test.default_split` (len==2, [10,90], sum==100) | S |
| CC-WIRE | Riscrivere `use-send-message`/`use-update-notes` con `createReloopClient`; rimuovere dir route morte (rmdir, non git rm) | S |

---

## 5. Funzioni da aggiungere — checklist per UC

**UC-01**
- [ ] `LeadRepository.update_contact_fields(lead_id, *, name=None, email=None)` fill-only `[→G-CONTACT]`
- [ ] ActionKind `capture_contact` + `CaptureContactHandler` in runtime, oppure scrittura lead in `BookSlotHandler.__call__`
- [ ] Routing ContactUpdate in `handle_ghl_event` `[→G-GHL-EVENT]`
- [ ] Default `auto_reply_enabled=true` a creazione merchant
- [ ] (L) `download_media` + `transcribe_audio`/`ocr_image`, preservare media_id in `webhook.py` parser
- [ ] `test_uc01_inbound.py` (gate auto_reply OFF, media placeholder)

**UC-02**
- [ ] `workers/scheduler/appointment_reminder.py:send_appointment_reminders(ctx)` + cron + persistenza booking_id/slot_start
- [ ] ActionKind `propose_slots` + `ProposeSlotsHandler`
- [ ] `_is_slot_conflict(e)` per discriminare 409/422 da 500/timeout
- [ ] `GHLClient.list_calendars(location_id)` + `GET /integrations/ghl/{merchant_id}/calendars` + `<select>`
- [ ] Risolvere `BOOKING_LOOKAHEAD_DAYS` in `_try_book`
- [ ] Routing AppointmentCreate/Update/Delete in `handle_ghl_event` `[→G-GHL-EVENT]`

**UC-03**
- [ ] `handle_call_outcome(ctx, *, merchant_id, contact_phone, outcome, ghl_contact_id)` `[BLOCKER B4]`
- [ ] Routing call-log → handle_call_outcome in `handle_ghl_event` `[→G-GHL-EVENT]`
- [ ] Campi testo reminder in bot-config-panel.tsx
- [ ] Testi/flow per attempt 3-4 o cap a 2

**UC-04**
- [ ] `GHLClient.add_contact_note(contact_id, *, body)` `[→G-GHL-NOTE]`
- [ ] `customFields` in `upsert_contact`/`create_opportunity` + `ghl.custom_field_map` `[→G-GHL-CUSTOMFIELD]`
- [ ] `TurnContext.lead_sentiment` + `collected_data`; comporre nota in `MovePipelineHandler._execute`
- [ ] `contact_fields` nello schema `move_pipeline` (orchestrator.py:134) o enrichment da lead `[→G-CONTACT]`
- [ ] `test_move_writes_internal_note_with_sentiment`

**UC-05**
- [ ] `LeadRepository.merge_content_signals(lead_id, new_signals)` OR-booleano in lead.meta
- [ ] `isinstance(signals, dict)` guard in `derive_signals_from_llm_payload` + test list/str

**UC-06**
- [ ] `LeadRepository.is_opted_out`/`mark_opted_out` + colonna `leads.opted_out_at`+RLS+migration
- [ ] Intercept STOP/CANCELLA in `handle_inbound_persist`; filtro in `_maybe_send`
- [ ] Filtro `Lead.status` in `list_reactivation_candidates` + **scrittura status terminale post-conversione**
- [ ] `Conversation.auto_reply`/`status` nel filtro candidati (skip takeover umano)
- [ ] Interpolazione `{name}`/`{last_topic}` in `decide_outbound`; `name`/`last_topic` su `ReactivationCandidate`
- [ ] Mostrare sezione reactivation in UI (rimuovere da HIDDEN_SECTIONS)
- [ ] `test_uc06_reactivation.py`

**UC-07**
- [ ] `KnowledgeBaseRepository.delete_doc` + `DELETE /docs/{doc_id}` + bottone UI
- [ ] Input URL in kb-uploader.tsx; azione "Re-indicizza" (endpoint esiste)
- [ ] `status_detail`/`last_error` in `KbDocOut` + render
- [ ] `test_uc07_retriever.py`, `test_uc07_extractor.py`

**UC-08**
- [ ] `override_rules` su `PlaygroundTurnIn` + `apply_playground_rule_overrides` + pannello regole UI
- [ ] `POST /playground/apply` (o riuso PUT overrides) + bottone Salva
- [ ] `test_playground_router.py`

**UC-09**
- [ ] `variant_id` su `TurnContext` + 3 emit `[BLOCKER B3]`
- [ ] `POST /{id}/stop` + `ABRepository.stop(id, winner)` + bottone UI
- [ ] `two_proportion_ztest` (modulo `ab_stats.py`) + `p_value`/`significant`/`winner` in metrics
- [ ] `.order_by(started_at)` + guard single-running in `start_experiment`
- [ ] `test_ab_metrics_attributes_conversion_to_variant`

**UC-10**
- [ ] `ConfigDict(extra='forbid')` su `BotConfigSchema` + sub-model
- [ ] Componente `TemplateFieldEditor` condiviso (estrai da bot-config-panel)
- [ ] `DELETE /templates/{id}` + `BotTemplateRepository.delete`
- [ ] Decidere `BotConfig.template_id` (cablare in resolver o rimuovere)
- [ ] Test strip locked_keys + bypass impersonation

**UC-11**
- [ ] Colonna `Lead.campaign` + migration + propagazione events + param `campaign` + `GET /analytics/merchant/campaigns` + dropdown
- [ ] State `sinceDays` + `<Select>` periodo
- [ ] Policy RLS tenant-OR-merchant su `analytics_events` `[→G-ANALYTICS-RLS]`
- [ ] `Lead.created_at >= since` in `merchant_kpis`/`score_distribution`
- [ ] `test_uc11_analytics.py`

**UC-12**
- [ ] `<tr>` ranking → `Link /merchants/{id}`
- [ ] Cablare o rimuovere `daily_kpi_rollup` (+ soglia hot da `ConfigKey`)
- [ ] `.where(Lead.created_at >= since)` in `merchants_ranking`/`tenant_totals`
- [ ] Bottone "Esporta CSV" `[→ccwire-export-ui]`
- [ ] `test_uc12_agency_dashboard.py`

**UC-13**
- [ ] Colonna `Objection.bot_variant` + migration + param `bot_variant` in repo/endpoint
- [ ] `GET /reports/objections/agency` + `category_histogram_tenant` + pagina web-admin
- [ ] ConfigKey `objections.categories` + risoluzione in `extract_for_conversation`
- [ ] Selettore periodo in objection-report.tsx
- [ ] `conversation.idle_close_minutes` da config in `close_idle_active`
- [ ] `test_uc13_objections.py`

**CC-FT**
- [ ] `build_italian_nlp_engine` per presidio `[BLOCKER B2]`
- [ ] `merchant_id` end-to-end fino a `FTModel(...)` (attiva A/B rollout)
- [ ] Split train/holdout in `export_training_pairs` + propagare `test_set_path`
- [ ] `schedule_fine_tune_runs(ctx)` cron
- [ ] `ft_experiment_settle(ctx, experiment_id)` cron (promozione/rollback)
- [ ] `test_ft_routing.py` per ModelRouter.select

**CC-TENANCY**
- [ ] `SET LOCAL ROLE` in `tenant_session` `[BLOCKER B1]`
- [ ] Ruolo `app_test` + `SET LOCAL ROLE` nel conftest integration
- [ ] Policy RLS merchant su `analytics_events`+`ft_models` `[→G-ANALYTICS-RLS]`
- [ ] Filtro esplicito in `TenantRepository.list_visible/get`
- [ ] `audit_service_role` marker nei worker

**CC-GHL** — coperto da G-GHL-NOTE, G-GHL-CUSTOMFIELD, G-GHL-EVENT, + refresh proattivo token, health-check su `ghl_location_tokens`, `test_ghl_event.py`

**CC-WA**
- [ ] Gate `is_within_24h` in `send_outbound_whatsapp` `[→G-COMPOSER-24H]`
- [ ] `last_inbound_at` in OpenAPI/types + banner UI + selettore template
- [ ] `POST /conversations/{id}/messages` con payload template + ramo `send_template`
- [ ] `test_webhook_signature.py`, `test_outbound_composer.py`

**CC-CONFIG**
- [ ] `is_within_active_hours` + gate orario `[→cfg-active-hours]`
- [ ] `field_validator` su `ab_test.default_split`

**CC-WIRE**
- [ ] Rimuovere `as never` (124 call-site) + eslint rule
- [ ] Cablare 4 endpoint: analytics exports, objections extract, KB reindex, (+ campaign)
- [ ] `use-send-message`/`use-update-notes` su `createReloopClient`
- [ ] `rmdir` dir route morte

---

## 6. Sequenza consigliata

Raggruppata per file condivisi e dipendenze.

**Sprint 0 — Blocker sicurezza/pipeline (≈3gg)**
1. **B1** `tenant_session` SET ROLE → **B-isolation-tests** (stessa area conftest/session.py). *Dipendenza: i test devono girare sotto ruolo non-priv.*
2. **B2** presidio italiano (isolato, sblocca tutta CC-FT).
3. **B3** variant_id in TurnContext+emit (sblocca UC-09 metriche).

**Sprint 1 — Lead identity & GHL hub (≈4gg)** — *tutto attorno a `handlers.py:270`, `conversation_service.py:887`, `actions/`, `ghl/client.py`*
4. **G-CONTACT** `update_contact_fields` (sblocca UC-01 scoring + UC-04 fallback).
5. **G-GHL-EVENT** routing `handle_ghl_event` (chiude pezzi di UC-01/02/03/CC-GHL in un colpo) → include **B4** (UC-03 call_outcome).
6. **G-GHL-NOTE** + **G-GHL-CUSTOMFIELD** (UC-04 + CC-GHL contrattuale, stesso client + `_execute`).

**Sprint 2 — Scoring, A/B, FT routing (≈3gg)**
7. uc05-content-signals + uc05-bool-validation (stessa area scoring).
8. ft-deploy-no-ab + ft-eval-trainset + ft-no-cron (stessa pipeline FT, dopo B2).
9. uc09-stop + uc09-significance + single-running (chiudono UC-09 dopo B3).

**Sprint 3 — RLS analytics & WhatsApp composer (≈3gg)**
10. **G-ANALYTICS-RLS** migrazione 0017 (chiude UC-11 leak + CC-TENANCY).
11. **G-COMPOSER-24H** + composer-ui + template-send (CC-WA, stesso `handlers.py:549` + composer.tsx).
12. cfg-active-hours (CC-CONFIG, gate in conversation_service — coordinare con eventuale rework turno).

**Sprint 4 — Booking completo & UI/wire (≈4gg)**
13. uc02-reminder (L) + proactive-slots + slot-taken + calendar-picker (tutto UC-02, dopo G-GHL-EVENT per appuntamenti sync).
14. **ccwire-as-never** (rischio regressione: fare DOPO che gli endpoint nuovi sono stabili) + cablaggio 4 endpoint (export, extract, reindex) + conversations hooks.
15. uc11-campaign (L) + period-ui.

**Sprint 5 — Gestione & UI residue + test (≈4gg)**
16. uc06-* (opt-out, status-filter, UI) + uc07-delete/url/reindex + uc10-* (extra-forbid, field-editor, delete) + uc13-* (bot-filter, agency-view, categories).
17. **Batch test**: uc06/uc11/uc12/uc13 + router playground + webhook signature + composer + ft-routing + isolation. *Da fare in coda per coprire le fix appena scritte.*

**Dipendenze critiche**: B2→tutta CC-FT; B1→tutti i test isolamento; B3→metriche UC-09 e ft A/B settle; G-CONTACT→UC-05 has_name/has_email e UC-04 fallback; G-GHL-EVENT→UC-02 reminder validi (no reminder su appuntamento cancellato). Rimandare `ccwire-as-never` finché gli endpoint nuovi non sono cablati (eviti doppio lavoro di tipizzazione).

---

## 7. Stima effort

Conteggio sui 78 gap confermati (accorpamenti contati una volta sola: G-CONTACT, G-GHL-EVENT, G-GHL-NOTE, G-GHL-CUSTOMFIELD, G-ANALYTICS-RLS, G-COMPOSER-24H riducono ~6 doppioni).

| Effort | Conteggio (netto) | Giorni |
|--------|-------------------|--------|
| S (0.5gg) | ~31 | 15.5 |
| M (1.5gg) | ~33 | 49.5 |
| L (4gg) | 4 (uc02-reminder, uc01-media, uc11-campaign, +1) | 16.0 |
| **Totale** | **~68** | **≈81 gg-uomo** |

### Per area

| Area | Giorni (≈) | Nota |
|------|-----------|------|
| Conversazione (UC-01→06) | ~28 | Più pesante: GHL hub, booking, riattivazione, media (L) |
| Piattaforma (UC-07→10) | ~14 | KB, playground, A/B, template editor |
| Analytics (UC-11→13) | ~16 | Campagna (L), RLS, dashboard, report agency |
| Trasversali (CC-*) | ~23 | FT, tenancy/RLS, GHL note/custom, WhatsApp composer, wiring |

**Stima realistica V1 "tutto funzionante": ~16-18 settimane-uomo** (81gg netti + ~20% per integrazione/QA con servizi esterni OpenAI FT, spaCy, Supabase/Redis/360dialog). **Per un "demo-ready" sui 13 UC core** (solo blocker + i ~6 accorpamenti major + auto_reply default): **≈3 settimane** (Sprint 0+1+2).