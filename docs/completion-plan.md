# Reloop AI — Piano di completamento a V1 al 100%

**Stato documento:** vivo · creato 2026-05-28
**Base:** audit dei 13 casi d'uso vs `reloop-ai-architettura.md` (sez. 10). Questo piano porta la piattaforma da "9/13 UC completi, 4 parziali + pipeline FT incompleta" a **tutti i 13 UC funzionanti end-to-end + pipeline fine-tuning + hardening di produzione**.

> Convenzione: ogni task ha **Obiettivo · File · Approccio · Done quando · Effort (S/M/L) · Dipende da**. Effort è giornate-uomo indicative per uno sviluppatore: S ≈ ≤0.5gg, M ≈ 1–2gg, L ≈ 3–5gg.

---

## Stato di implementazione — branch `feat/complete-use-cases` (2026-05-28)

Tutto il piano è stato implementato su questo branch. Riepilogo:

| Task | Stato | Note |
|------|-------|------|
| 1.1 Prompt Manager + variant-aware | ✅ | `PromptManager`/`PromptRepository`; A/B autora i prompt per variante |
| 1.2 Action-steering prompt | ✅ | schema hint con regole + payload per ogni azione |
| 1.3 Sentiment Analyzer | ✅ | `SentimentAnalyzer` (gpt-5-nano), persiste `lead.sentiment` |
| 1.4 Scoring always-on cumulativo | ✅ | `derive_conversation_signals` + iniezione `update_score` |
| 1.5 Realtime dashboard | ✅ | migr. 0013 pubblica `analytics_events` + refetch 30s |
| 1.6 Auto-trigger obiezioni + heatmap | ✅ | cron `close_idle_conversations` + `ObjectionHeatmap` |
| 2.1 Orchestrazione FT | ✅ | `fine_tune_run` concatena la catena; router `/fine-tuning` + UI admin |
| 2.2 presidio NER | ✅ | dipendenza + transform con degradazione regex-only |
| 2.3 quality_filter | ✅ | `workers/fine_tuning/quality.py` |
| 2.4 evaluator reale | ✅ | held-out vs baseline con gate pass-margin |
| 2.5 FT routing + A/B rollout | ✅ | `FtModelResolver` variant-aware; deploy crea esperimento |
| 3.1 test + CI | ✅ (parziale) | +26 unit test; isolation RLS già in CI; OpenAPI types rigenerati |
| 3.2 testi config + media | ✅ | override testi reminder/riattivazione; placeholder media |
| 3.3 UI config tre stati | ✅ (già presente) | `bot-config-panel.tsx` già implementa Inherited/Customized/Locked |
| 3.4 observability | ✅ (già presente) | Sentry+PostHog già in `shared.observability`; structlog→Railway |
| 3.5 pulizia | ✅ | route morte rimosse, job-id reindex, lint `ruff check` verde |

**Verifica:** 100 unit test verdi, `ruff check .` pulito, frontend typecheck+lint verdi, app/worker importano (17 job, 5 cron). **Non verificabile localmente** (richiede servizi esterni): chiamate OpenAI FT, modello spaCy presidio, flusso conversazione live su Supabase/Redis/360dialog. **Residuo 3.1:** test per UC DB-bound (04/06/08/10/11/12/13) demandati ai test di integrazione (l'infra CI con Postgres c'è già).

---

## Addendum — hardening agente + booking (2026-06-08)

Chiusura dei tool dell'AI agent e robustezza del booking via messaggio. Tutto verificato a unit (**108 test verdi**, da 100), `ruff check` pulito sui file toccati, frontend typecheck verde, smoke-import worker+API OK.

| Item | Stato | Dettaglio |
|------|-------|-----------|
| Tool `escalate_human` | ✅ | Era definito in `orchestrator.py` ma **senza handler** (no-op). Aggiunto `actions/escalate.py` (`EscalateHumanHandler`): rispetta `escalation.enabled`, fa takeover umano (`conversation.auto_reply=False` via `ConversationRepository.mark_escalated`), stampa `meta.escalated`/reason ed emette `conversation.escalated`. Registrato in `workers/runtime.py`. → l'agente ha ora **tutti** i 5 tool funzionanti. |
| `move_pipeline` standalone | ✅ | Prima falliva (`opportunity_required`) se non c'era stato un `book_slot`. Ora `actions/pipeline.py` **crea l'opportunity nello stage target** quando manca (pipeline+location dai config). |
| Persistenza refresh token GHL | ✅ | `GHLClient._refresh_token` aggiornava i token solo in memoria → dopo la rotazione del refresh token il booking si rompeva. Aggiunto callback `on_token_refresh`; booking/pipeline lo agganciano a `IntegrationRepository.upsert_ghl`. Corretto anche il calcolo `expires_at` (da `expires_in`). |
| Timezone booking | ✅ | `_parse_iso` interpretava gli orari naïve dell'LLM come **UTC**; ora usa `schedule.timezone` del merchant (default Europe/Rome) → il lead prenota all'ora locale corretta. |
| Endpoint conversations | ✅ | `routers/conversations.py` `list_conversations`/`get_conversation` lanciavano `NotImplementedError` (500 latente). Implementati come read RLS-protette mantenendo le firme → nessun drift OpenAPI. |
| Default auto-reply OFF | ✅ | Kill-switch `bot.auto_reply_enabled` ora parte spento (scelta di prodotto, toggle in `bot-config-panel.tsx`). Allineati i 2 unit test UC-01 che assumevano il default ON. |

**Nuovi test:** `test_escalate.py` (2), `test_uc04_pipeline.py` (3), `test_ghl_client.py` (2), `test_uc02_booking.py` +1 (timezone).

### Go-live audit — blocker confermati e risolti (2026-06-08)

Audit multi-agente di production-readiness (12 dimensioni, verifica avversariale di ogni blocker). 15 blocker grezzi → **2 confermati** (entrambi bug di codice sul percorso core, ora risolti) + 13 declassati (ops/config). **112 unit test verdi**, `ruff check`/`ruff format` puliti sui file toccati, frontend typecheck verde, OpenAPI client rigenerato (offline mode).

| Blocker confermato | Fix |
|--------------------|-----|
| GHL `upsert_contact` chiamava `POST /contacts/` (create) **senza `locationId`** → ogni contatto/booking/pipeline-move 400 | `client.py`: ora `POST /contacts/upsert` con `locationId` iniettato dal token bundle. Corretti anche `create_booking` (aggiunge `locationId`) e `get_free_slots` (param **epoch-ms** invece di ISO + parsing della response per-data). |
| Callback OAuth GHL usava `DBSession` tenant-scoped → JWT forzato su un redirect browser senza auth → **403 per ogni merchant** che collega il CRM | `integrations.py`: callback ora su `session_scope()` service-role (identità dal `state` firmato, non da RLS). Lo schema OpenAPI rigenerato conferma la rimozione del param `Authorization`. |

**Nuovi test:** `test_ghl_client.py` +3 (upsert endpoint+locationId, booking locationId, free-slots epoch-ms+flatten), `test_integrations_oauth.py` (1, callback senza JWT). Restano i 13 declassati (vedi risposta go-live: hook Supabase da abilitare, segreti prod, app GHL/360dialog, job retention, DSAR, modello spaCy nel worker image, debito CI format/mypy).

### Block B — hardening produzione + review avversariale (2026-06-10)

Chiusi i 6 item di codice "block B" del go-live, poi un workflow di review avversariale (4 dimensioni) ha trovato **1 blocker** (subito risolto) + warning (chiusi). **131 unit test verdi**, ruff/format puliti, mypy nuovo-codice clean, frontend typecheck verde, client OpenAPI rigenerato (DSAR + default auto_reply).

| Item | Stato | Dettaglio |
|------|-------|-----------|
| Model-id via env | ✅ | `settings.llm_model_{default,escalation,sentiment,fallback,embedding}` usati in `router.py`/`runtime.py`/`main.py` + i due call-site residui (`objections.py`, `evaluate.py`) — niente più ID hardcoded. |
| Fail-fast settings | ✅ | `Settings.ensure_production_ready()` (segreti core + db/redis non-localhost) chiamato allo startup API e worker; warning per integrazioni opzionali. |
| spaCy + presidio require | ✅ | `worker.Dockerfile` scarica `it_core_news_lg`; `build_presidio_transform(require=…)` → in produzione l'export FT fallisce se manca la NER invece di degradare. |
| Retention GDPR | ✅ | `workers/scheduler/retention.py` (cron 03:30) + `ConversationRepository.delete_older_than`/`merchants_with_conversations_before`; floor di sicurezza sui mesi, drain a batch con commit, budget per-run. |
| Rate-limit + TrustedHost | ✅ | `RateLimitMiddleware` (Redis fixed-window, fail-open) solo sul callback OAuth (no webhook da IP BSP condiviso); `TrustedHostMiddleware` opt-in via `ALLOWED_HOSTS`. |
| DSAR | ✅ | `routers/dsar.py` export + erase (hard-delete conversazioni, strip PII), ristretto ai ruoli `agency_admin`/`merchant_admin`. |
| **Blocker review** | ✅ | Rotazione refresh-token GHL: `_persist_tokens` scriveva nella transazione dell'handler (rollback su errore successivo → token perso). Ora persiste in una `session_scope()` propria e committata. |

**Nuovi test (block B):** `test_settings_validation.py`, `test_presidio_require.py`, `test_ratelimit.py`, `test_retention.py` (incl. floor-clamp + drain multi-batch), `test_dsar.py`.

### Block B — follow-up di codice (2026-06-10)

Chiusi i due follow-up rimasti. **132 unit test verdi**, ruff/format puliti, mypy nuovo-codice clean.

- **Retention a livello lead:** `LeadRepository.anonymize_stale` (strip PII dei lead oltre il cutoff **senza conversazioni residue**; phone→tombstone, status `erased`, idempotente) integrato nel cron `enforce_retention` dopo il purge conversazioni. Il return/analytics ora riportano `leads_anonymized`.
- **`delete_merchant` ordering:** ora DELETE+commit del DB **prima**, poi rimozione auth Supabase **best-effort** (niente più 500 a metà loop con merchant mezzo-cancellato; i fallimenti si loggano per riconciliazione, conteggiati in `auth_user_failures`).

**Restano (non-codice o decisione):** gate ops di go-live (hook Supabase, segreti prod, app GHL, router 360dialog, provisioning Railway), TrustedHost/`/health` (solo doc), e il debito CI legacy `ruff format`/`mypy` (PR dedicata, no mass-reformat opportunistico).

---

## Stato attuale (sintesi audit)

| UC | Nome | Stato | Gap principale |
|----|------|-------|----------------|
| UC-01 | First Response | ✅ Completo | solo testo/interactive (media scartati) |
| UC-02 | Booking Autonomo | ✅ Completo | dipende dall'azione `book_slot` poco guidata dal prompt |
| UC-03 | Senza Risposta | ✅ Completo | testi reminder hardcoded |
| UC-04 | Pipeline Auto | ⚠️ Parziale | **Sentiment Analyzer inesistente** |
| UC-05 | Lead Scoring | ⚠️ Parziale | scatta solo su `signals` LLM; overwrite single-turn |
| UC-06 | Riattivazione Dormienti | ✅ Completo | testi hardcoded |
| UC-07 | Knowledge Base | ✅ Completo | — |
| UC-08 | Playground | ✅ Completo | — |
| UC-09 | A/B Testing | ❌ Parziale (premessa rotta) | **le varianti girano lo stesso prompt** |
| UC-10 | Bot Default Agenzia | ✅ Completo | UI a textarea JSON, non per-campo |
| UC-11 | Dashboard Merchant | ⚠️ Parziale | **Realtime morto** (tabella non pubblicata) |
| UC-12 | Dashboard Admin | ⚠️ Parziale | stesso gap Realtime |
| UC-13 | Report Obiezioni | ⚠️ Parziale | estrazione mai auto-triggerata; report a barre, non heatmap+trend |
| — | Pipeline fine-tuning | ⚠️ Parziale | nessuna orchestrazione/trigger; presidio assente; evaluator stub |

---

## Workstream 1 — Far funzionare davvero gli UC parziali

Massimo ROI, nessuna nuova infrastruttura. Trasforma 4 UC parziali in completi e rende affidabili UC-02/04/05.

### 1.1 — Prompt Manager + prompt variant-aware (sblocca UC-09)

- **Obiettivo:** le varianti A/B devono produrre comportamenti diversi. Oggi `prompt_templates` esiste ma non è mai letta; `_resolve_system_prompt` non riceve `variant_id`; l'orchestrator ignora `ctx.variant_id`.
- **File:**
  - `backend/libs/ai_core/src/ai_core/` → nuovo `prompt_manager.py` (classe `PromptManager`).
  - `backend/libs/db/src/db/repositories/` → nuovo `prompt.py` (`PromptRepository`).
  - `backend/libs/ai_core/src/ai_core/conversation_service.py:479` `_resolve_system_prompt(..., variant_id)`.
  - `backend/libs/db/src/db/models/ab.py` (i variants JSONB già contengono `prompt_template_id`).
  - `backend/libs/ai_core/src/ai_core/playground.py` (passare variant_id reale).
- **Approccio:** `PromptManager.resolve(merchant_id, variant_id)` → se l'esperimento attivo assegna un `prompt_template_id`, carica `prompt_templates` per `(merchant_id, variant_id, version)` e usa quel system prompt; altrimenti fallback all'attuale cascade di config. Threading di `variant_id` da `_assign_ab_variant` (`conversation_service.py:580`) fino al system prompt.
- **Done quando:** un esperimento con due `prompt_template_id` distinti produce risposte diverse per lead assegnati a varianti diverse; test unit che assegna 2 lead a 2 varianti e verifica prompt diversi.
- **Effort:** M · **Dipende da:** —

### 1.2 — Steering delle azioni nel prompt orchestrator (affidabilità UC-02/04/05)

- **Obiettivo:** oggi `_RESPONSE_SCHEMA_HINT` (`orchestrator.py:115`) elenca i `kind` di azione ma non dice **quando** emetterle né quali campi `payload` riempire. Le azioni `book_slot`/`move_pipeline`/`update_score` scattano solo se il modello indovina.
- **File:** `backend/libs/ai_core/src/ai_core/orchestrator.py:115-124` (e potenziale estrazione in un template versionato sotto PromptManager).
- **Approccio:** arricchire l'istruzione di sistema con: regole su quando emettere ciascuna azione, schema dei `payload` (`book_slot`: `preferred_start_iso`, `calendar_id`, `contact_fields`; `move_pipeline`: stage target; `update_score`: `signals` con chiavi whitelisted). Aggiungere few-shot brevi.
- **Done quando:** test con conversazione "voglio prenotare giovedì alle 15" produce un'azione `book_slot` con `preferred_start_iso` valorizzato.
- **Effort:** S · **Dipende da:** 1.1 (idealmente lo steering vive nei template versionati)

### 1.3 — Sentiment Analyzer (UC-04, alimenta UC-05)

- **Obiettivo:** il componente Sentiment della sez. 10/11.1 non esiste. La colonna `lead.sentiment` (`models/lead.py:33`) non viene mai scritta. Il branch router `purpose="sentiment"` → `gpt-5-nano` (`router.py:59`) è codice morto.
- **File:**
  - nuovo `backend/libs/ai_core/src/ai_core/sentiment.py` (`SentimentAnalyzer`).
  - `backend/libs/ai_core/src/ai_core/conversation_service.py` (chiamata per turno inbound + persistenza).
  - `backend/libs/db/src/db/repositories/lead.py` (writer `sentiment`).
- **Approccio:** chiamata lightweight `gpt-5-nano` via `ModelRouter.select(RoutingRequest(purpose="sentiment", ...))` sull'ultimo turno utente → `{positive|neutral|negative}`; persistere su `lead.sentiment`; emettere analytics; passare il sentiment come input ai signals di scoring (1.4) e come nota nel `move_pipeline` (UC-04).
- **Done quando:** dopo un turno inbound, `lead.sentiment` è valorizzato e visibile in `ConversationViewer`; test unit del classifier.
- **Effort:** M · **Dipende da:** —

### 1.4 — Lead scoring always-on + cumulativo (UC-05)

- **Obiettivo:** oggi lo scoring scatta solo se l'LLM emette `update_score` con `signals` (`actions/scoring.py:53`), ed è replace-on-write (`scoring.py:68`). Un singolo turno negativo azzera un lead caldo. La derivazione di signal "engagement/timing" promessa nei docstring non è implementata.
- **File:**
  - `backend/libs/ai_core/src/ai_core/scoring.py` (derivazione signal da features conversazione + accumulo/EWMA).
  - `backend/libs/ai_core/src/ai_core/actions/scoring.py:5,53` (sempre-attivo post-turno, non solo su azione LLM).
  - `backend/libs/ai_core/src/ai_core/conversation_service.py` (invocare scoring ogni turno).
- **Approccio:** calcolare i signal da features reali (numero turni, tempi di risposta, completezza dati lead, sentiment da 1.3, dati qualificanti) **ogni turno**, non solo su volere dell'LLM. Passare a un accumulo (es. EWMA o somma pesata sullo storico) invece dell'overwrite. Mantenere `LeadScore(score, reason_codes)`.
- **Done quando:** lo score evolve a ogni turno senza richiedere l'azione LLM; un turno negativo riduce ma non azzera; test con sequenza di turni che mostra monotonia ragionevole.
- **Effort:** M · **Dipende da:** 1.3

### 1.5 — Realtime delle dashboard (UC-11, UC-12)

- **Obiettivo:** il frontend è sottoscritto correttamente a `postgres_changes` su `analytics_events`, ma la tabella non è mai aggiunta alla publication `supabase_realtime` (solo `messages`/`conversations` in 0008, note in 0012). Le dashboard non sono live.
- **File:**
  - nuova migrazione `backend/libs/db/src/db/migrations/versions/0013_realtime_publish_analytics.py` (mirror di `0008_realtime_publish_messages.py`: `ALTER PUBLICATION supabase_realtime ADD TABLE public.analytics_events` + `REPLICA IDENTITY FULL`).
  - `frontend/apps/web-merchant/src/components/dashboard/merchant-dashboard.tsx` e `web-admin/.../agency-dashboard.tsx` (aggiungere `refetchInterval` di fallback, es. 30s).
- **Approccio:** una migrazione + fallback polling per resilienza. Verificare che la RLS su `analytics_events` (già presente) isoli gli eventi per tenant nella subscription.
- **Done quando:** inserendo un `analytics_events` reale, la KPI card si aggiorna senza reload; il claim UI "si aggiorna quando arrivano nuovi eventi" diventa vero.
- **Effort:** S · **Dipende da:** —

### 1.6 — Auto-trigger estrazione obiezioni + trend/heatmap (UC-13)

- **Obiettivo:** il classifier e la persistenza sono reali ma `objection_extraction` non è in `cron_jobs` e non è mai enqueued alla chiusura conversazione (solo endpoint manuale `reports.py:51`). Il report è un istogramma a barre senza dimensione temporale; la spec chiede `ObjectionHeatmap` + trend.
- **File:**
  - **Trigger:** `backend/workers/scheduler/objections.py` + `backend/workers/settings.py:85` (`cron_jobs`) → aggiungere un **daily sweep** sulle conversazioni `status` non-attive senza obiezioni estratte. (Non esiste un evento di "chiusura" esplicito: `conversation.status` default `active` — vedi nota sotto.)
  - **Chiusura conversazione:** introdurre uno stato `closed`/`idle` (sweep su `last_message_at` oltre soglia in `workers/scheduler`), che diventa il trigger di estrazione.
  - **Trend backend:** `backend/libs/db/src/db/repositories/objection.py:61` → aggiungere `category_histogram_by_day` (group by giorno + categoria).
  - **Frontend:** `frontend/apps/web-merchant/src/components/reports/objection-report.tsx` → componente `ObjectionHeatmap` (categorie × tempo) sopra le barre esistenti.
- **Approccio:** sweep cron (es. ogni ora) che marca conversazioni idle come `closed` ed enqueue `objection_extraction`; il report aggiunge serie temporale e heatmap.
- **Done quando:** una conversazione lasciata idle viene chiusa e le sue obiezioni compaiono nel report senza intervento manuale; la heatmap mostra categorie × settimane.
- **Effort:** L · **Dipende da:** —

---

## Workstream 2 — Pipeline fine-tuning end-to-end (settimane 9–10)

Gli step esistono ma scollegati: `collect`/`export` non sono job ARQ e non hanno chiamanti; `quality_filter` manca; `evaluator` è un placeholder (`handlers.py:137`); presidio è citato ma non è dipendenza; il routing FT non è mai attivo.

### 2.1 — Orchestrazione e trigger della pipeline

- **Obiettivo:** catena `collect → export → train → evaluate → deploy` realmente eseguibile.
- **File:** `backend/workers/fine_tuning/{collect,export,handlers}.py`, `backend/workers/settings.py:55` (`functions`), nuovo `routers/fine_tuning.py` + mount in `main.py`, UI admin in `web-admin`.
- **Approccio:** registrare `collect_training_pairs` ed `export_training_pairs` come job ARQ; far concatenare gli step (ognuno enqueue il successivo o un orchestratore unico); endpoint `POST /fine-tuning/run/{merchant_id}` (service-role, loggato con `actor_id`) + pulsante in pannello admin/merchant.
- **Done quando:** un trigger manuale percorre l'intera catena fino a una riga `ft_models` in stato `deployed` (in staging con dataset di test).
- **Effort:** L · **Dipende da:** 2.2, 2.3, 2.4

### 2.2 — Layer NER presidio nell'anonimizzazione (Art. 5.2, contrattuale)

- **Obiettivo:** oggi solo regex (`ft/anonymizer.py`). La spec e l'Art. 5.2 impongono **presidio + regex** (nomi, luoghi, organizzazioni).
- **File:** `backend/libs/ai_core/pyproject.toml` (dipendenza `presidio-analyzer`), `backend/libs/ai_core/src/ai_core/ft/anonymizer.py`.
- **Approccio:** aggiungere presidio come dipendenza reale; pipeline a doppio layer (presidio NER → regex tipizzata già esistente); report di redazione esteso.
- **Done quando:** un dataset con nomi/indirizzi viene anonimizzato anche per le entità non coperte da regex; test su PII di esempio.
- **Effort:** M · **Dipende da:** —

### 2.3 — Step `quality_filter`

- **Obiettivo:** lo step della sez. 5.4 non esiste; l'unico gate è il filtro grezzo `status IN (booked, qualified)`.
- **File:** nuovo `backend/workers/fine_tuning/quality.py`, invocato tra `collect` ed `export`.
- **Approccio:** scartare conversazioni con bot-error, dropoff prematuri, turni insufficienti, lingua errata; soglie configurabili.
- **Done quando:** export esclude conversazioni sotto-soglia; test su dataset misto.
- **Effort:** M · **Dipende da:** —

### 2.4 — Evaluator reale (gate su threshold)

- **Obiettivo:** sostituire il placeholder `{"pass": True}` (`handlers.py:137`) con valutazione vera su test set held-out (spec 11.2).
- **File:** `backend/workers/fine_tuning/handlers.py:137` + nuovo `evaluator.py`.
- **Approccio:** held-out set, metriche custom (aderenza azioni, qualità reply vs baseline `gpt-5-mini`), gate `delta > threshold` che blocca il deploy se non superato.
- **Done quando:** il deploy avviene solo se le metriche superano la soglia; le metriche sono persistite in `ft_models`.
- **Effort:** L · **Dipende da:** 2.1

### 2.5 — Routing modello FT + rollout via A/B (non flag flip)

- **Obiettivo:** `FtModelProvider` esiste (`router.py:50`) ma entrambi i call-site di `ModelRouter(...)` lo omettono (`runtime.py:66`, `main.py:53`), quindi il modello FT non sostituisce mai il default. La spec (6.7) vuole rollout via A/B (UC-09), non flag flip; oggi `fine_tune_deploy` fa solo `is_default = true`.
- **File:** `backend/libs/ai_core/src/ai_core/router.py:50`, `backend/workers/runtime.py:66`, `backend/services/api/src/api/main.py:53`, `backend/workers/fine_tuning/handlers.py:168`.
- **Approccio:** implementare e iniettare `FtModelProvider` (lookup `ft_models.is_default` per tenant con cache); al deploy, creare un esperimento A/B baseline-vs-FT (UC-09) invece del flag flip diretto.
- **Done quando:** un tenant con FT deployed instrada al modello FT per la variante FT dell'esperimento; baseline resta sull'altra variante.
- **Effort:** M · **Dipende da:** 1.1, 2.1

---

## Workstream 3 — Hardening, allineamento spec, produzione

### 3.1 — Copertura test + isolamento RLS in CI + drift OpenAPI

- **Obiettivo:** mancano test per UC-04, 06, 08, 10, 11/12, 13; i 14 test di isolamento RLS richiedono DB e non girano qui.
- **File:** `backend/tests/unit/` (nuovi `test_uc04_*`, `test_uc06_*`, `test_uc08_*`, `test_uc10_*`, `test_uc11_*`, `test_uc13_*`), `.github/workflows/` (job con Postgres per `tests/integration`), `scripts/generate-api-types.sh` (drift check CI).
- **Done quando:** ogni UC ha almeno un test; CI esegue gli isolation test con 2 tenant; il drift OpenAPI fallisce su tipi non rigenerati.
- **Effort:** L · **Dipende da:** WS1 (i test seguono le feature corrette)

### 3.2 — Testi messaggi configurabili + gestione media WhatsApp (UC-01/03/06)

- **Obiettivo:** i testi reminder/riattivazione sono hardcoded in italiano (`no_answer.py:35`, `reactivation.py`); UC-01 scarta silenziosamente messaggi non-testo (`webhooks.py:79`).
- **File:** `backend/libs/config_resolver/.../schema.py` (nuove chiavi testo), `backend/workers/scheduler/{no_answer,reactivation}.py`, `backend/services/api/src/api/routers/webhooks.py`.
- **Approccio:** spostare i testi nella cascade di config; gestire almeno un fallback educato per media (immagine/audio) invece dello scarto silenzioso.
- **Done quando:** i testi sono modificabili da pannello; un'immagine inbound riceve una risposta gestita.
- **Effort:** M · **Dipende da:** —

### 3.3 — UI config a tre stati Inherited/Customized/Locked (spec 9.5)

- **Obiettivo:** oggi l'editor template/override è una textarea JSON; la spec 9.5 chiede form per-campo con stati Inherited/Customized/Locked-by-agency.
- **File:** `frontend/apps/web-admin/src/components/templates/templates-panel.tsx`, `frontend/apps/web-merchant/src/components/bot-config/bot-config-panel.tsx`.
- **Approccio:** form generato dallo schema Pydantic esportato via OpenAPI; badge di provenienza valore + link "ripristina default" + read-only sui locked.
- **Done quando:** ogni parametro mostra origine e stato; i locked sono read-only lato merchant.
- **Effort:** M · **Dipende da:** —

### 3.4 — Observability completa (Sentry, log aggregation)

- **Obiettivo:** PostHog è già wired (`main.py:46`); mancano Sentry (frontend+backend) e aggregazione log strutturati (Logtail/Grafana) della sez. 13.5.
- **File:** `backend/services/api/src/api/main.py`, `backend/workers/runtime.py`, frontend apps, `infra/`.
- **Done quando:** errori tracciati su Sentry con release tagging; log `structlog` interrogabili per `trace_id`.
- **Effort:** M · **Dipende da:** —

### 3.5 — Pulizia e fix minori

- **Obiettivo:** rimuovere route dir morte (`web-merchant/src/app/dashboard/`, `web-merchant/src/app/reports/objections/` vuote; analoghe in admin) che ombreggiano le route reali sotto `(app)/`; fix dedup job-id reindex (`knowledge_base.py:81` usa `request.state.ts` mai settato).
- **File:** route dir vuote frontend; `backend/services/api/src/api/routers/knowledge_base.py:81`.
- **Done quando:** nessuna route morta; il dedup reindex usa una chiave valida.
- **Effort:** S · **Dipende da:** —

---

## Sequenza consigliata

1. **Sprint 1 (correttezza UC):** 1.5 (S) → 1.1 (M) → 1.2 (S) → 1.3 (M) → 1.4 (M) → 1.6 (L). Porta UC-04/05/09/11/12/13 a completi. Massimo valore percepito.
2. **Sprint 2 (FT):** 2.2 (M) → 2.3 (M) → 2.4 (L) → 2.1 (L) → 2.5 (M). Completa il differenziatore delle settimane 9–10.
3. **Sprint 3 (hardening):** 3.1 (L) in parallelo a tutto; poi 3.2/3.3/3.4/3.5.

## Definition of Done — checklist "100%"

- [ ] UC-01..13 ognuno con almeno un test verde + verifica manuale end-to-end documentata in `docs/runbooks/`.
- [ ] A/B: due varianti con prompt diversi producono comportamenti misurabilmente diversi (1.1).
- [ ] Sentiment scritto su ogni lead e usato in pipeline + scoring (1.3, 1.4).
- [ ] Dashboard merchant e admin si aggiornano in tempo reale (1.5).
- [ ] Obiezioni estratte automaticamente + heatmap/trend (1.6).
- [ ] Pipeline FT eseguibile end-to-end con anonimizzazione presidio+regex e gate su evaluation (WS2).
- [ ] Modello FT instradato per-tenant via rollout A/B (2.5).
- [ ] CI: lint + typecheck + test + isolation RLS 2-tenant + drift OpenAPI (3.1).
- [ ] Observability: Sentry + log aggregation attivi (3.4).
- [ ] Nessuna route morta; testi bot configurabili; UI config a tre stati.

## Note e rischi

- **Nessun evento di "chiusura conversazione"** esiste oggi (`conversation.status` default `active`): UC-13 auto-trigger e l'evaluation FT dipendono dall'introdurre uno stato `closed`/`idle` via sweep (1.6). È un prerequisito trasversale.
- **Deviazione canale WhatsApp:** la spec parla di "WhatsApp Cloud API (Meta) BSP diretto", il codice usa **360dialog** (`integrations/whatsapp/d360_client.py`) con coexistence e un layer `integrations/router`. Il piano assume 360dialog come canale di produzione; aggiornare la spec o un ADR per ufficializzare la scelta.
- **Prompt Manager è il cardine:** 1.1 sblocca UC-09 *e* la qualità di UC-02/04/05 (steering azioni) *e* il rollout FT (2.5). Va fatto presto.

## Estensione — Automazioni (lavagnetta) + validazione template (2026-06-19)

Vedi [ADR 0011](decisions/0011-automation-flow-builder-and-template-validation.md).

**A. Lavagnetta automazioni (visual flow builder).** Nuovo modello a grafo distinto
dai `flows`/`flow_steps` lineari: tabelle `automation_flows` / `automation_nodes` /
`automation_edges` (migrazione **0027**, RLS pattern 0014). Nodi `trigger | condition |
action`; logica grafo pura condivisa in `ai_core/automations.py` (validazione, eval
condizioni, traversata, rilevamento cicli) — unit-testata (`test_automations_graph.py`).
Router `/automations` (CRUD; bozza salvabile incompleta, abilitazione richiede grafo
valido). Motore worker `workers/automation/engine.py`: cron `automation_dispatch` che fa
tail di `analytics_events` (cursore Redis, zero modifiche al path conversazione) + job
`automation_run` che cammina il grafo, valuta condizioni, esegue azioni (template/messaggio
via 24h-window, `wait` con deferral ARQ). Trigger V1: `message_received`, `no_answer`,
`booking_created/failed`, `lead_dormant`. UI: route `/automazioni` con canvas React Flow
(`@xyflow/react`) — palette, nodi custom, rami sì/no, pannello config, salvataggio.

**B. Validazione template WhatsApp completa.** `lint_template` ora ritorna `LintIssue` con
`severity` (error blocca, warning avvisa) e `field`; copre l'intero ruleset Meta: formato
lingua + codice supportato, whitespace body (tab/spazi/righe = errore), esempi per `{{n}}`,
caps bottoni completi (≤10, URL≤2, phone≤1, copy-code≤1, https, label≤25, E.164),
AUTHENTICATION, warning promo-in-UTILITY. Endpoint nuovi: `POST /whatsapp-templates/validate`,
`PUT /{id}` (modifica draft/rifiutato), `POST /{id}/submit`, `as_draft` su create;
`body_examples` persistito (migrazione **0026**). UI: form completo (header testo, bottoni,
esempi per variabile), **anteprima a bolla WhatsApp**, percorso bozza→modifica→invio.
Unit test estesi (`test_whatsapp_templates.py`).

Stato: backend + frontend implementati, 356 unit test verdi, client OpenAPI rigenerato,
web-merchant typecheck + lint puliti. Migrazioni 0026/0027 riallineate a valle di
`0025_conversation_handoff`. Verifica end-to-end con servizi reali (360dialog, Postgres/Redis)
ancora da fare; estendere gli isolation test 2-tenant alle 3 nuove tabelle automation.
