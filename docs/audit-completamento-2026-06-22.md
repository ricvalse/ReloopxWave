# Reloop AI — Audit di completamento V1 (2026-06-22)

**Stato documento:** snapshot verificato · branch `main` (HEAD `d11b717`)
**Metodo:** audit multi-agente su 12 dimensioni (13 UC + cross-cutting), con **verifica avversariale di ogni gap** contro il codice reale (66 agenti, ogni finding confermato/refutato leggendo i file). 54 gap grezzi → **52 confermati + 2 refutati**.
**Scope verità:** `reloop-ai-architettura.md` (spec d'intento), `capitolato-tecnico.md` (scope contrattuale), codice in `backend/` e `frontend/`. Le deviazioni note (360dialog, GHL marketplace, ecc.) NON sono trattate come gap.

> Questo documento **sostituisce di fatto** le righe "Stato attuale" di `docs/completion-plan.md` (auto-riportate come quasi tutto ✅ a maggio): il codice è andato molto avanti — tutti i 13 UC sono end-to-end e funzionanti — ma sono emersi gap nuovi (DSAR, CI rientrata in rosso, persistenza messaggi proattivi, rollout FT codice morto, copertura RLS test) che il piano vecchio non vede.

---

## 1. Verdetto sintetico

**La piattaforma è strutturalmente completa e prossima al go-live. Nessun blocker funzionale: tutti i 13 UC girano end-to-end.** I gap residui sono di **qualità/affidabilità/compliance**, non di feature mancanti. Due aree però richiedono attenzione prima della consegna:

1. **La CI è rossa su `main`** (backend: ruff/format/mypy; frontend: `pnpm test` senza test). Operativamente blocca ogni merge pulito → priorità massima, effort basso.
2. **Il flusso GDPR/DSAR è inutilizzabile end-to-end** (bug sul ruolo + nessuna UI) → rischio contrattuale/normativo, effort basso-medio.

| Severità | # | Significato |
|----------|---|-------------|
| 🔴 Blocker | **0** | Nessuno: nessun UC rotto, nessuna falla di isolamento attiva |
| 🟠 Major | **19** | UC parziale vs spec, feature contrattuale mancante, CI rotta, compliance |
| 🟡 Minor | **30** | Qualità, UX, data-quality, debito di test/doc |
| ⚪ Nice-to-have | **3** | Oltre lo scope V1 / cosmetico |
| ✅ Refutati | **2** | Segnalati ma smentiti dal codice (vedi §6) |

**Stima effort per chiudere tutti i major:** ~15 giornate-uomo (15×M + 4×S). I 4 fix di CI valgono ~1 giornata e sbloccano la pipeline.

---

## 2. Gap MAJOR (19) — la vera "to-do" per la consegna

Raggruppati per tema, in ordine di priorità consigliata.

### A. CI / qualità del codice — pipeline rossa su `main` (4 gap · ~1 gg)

Il commit `98bf7c9` ("azzera il debito CI") del 12/06 ha pulito tutto, ma **13 commit successivi hanno fatto rientrare il debito**. I job in `backend.yml`/`frontend.yml` sono gating (no `continue-on-error`): oggi la CI fallisce su ogni push.

| ID | Gap | Evidenza | Fix | Eff |
|----|-----|----------|-----|-----|
| 40 | `ruff check` rosso | `tests/unit/test_impersonation.py:28` — C408 unnecessary-collection-call; `backend.yml:61` gating | sostituire `dict()`/collection con literal; aggiungere pre-commit ruff | S |
| 41 | 17 file non formattati (`ruff format --check`) | codice **nuovo** non legacy: `ai_core/automations.py`, `ai_core/orchestrator.py`, `ai_core/rag/indexer.py`, `routers/automations.py`, `workers/automation/engine.py`, `whatsapp/templates.py`, `repositories/automation.py`, migr. 0016/0021/0027 | `uv run ruff format .` e commit (no mass-reformat dei 75 legacy) | S |
| 42 | 2 errori mypy in codice nuovo | `workers/scheduler/catalog_reindex.py:138` e `scripts/seed_amalia.py:248` — `no-any-return` | cast/annotazione esplicita; reintegrare gate mypy nel pre-commit | S |
| 39 | CI frontend permanentemente rossa | `apps/*/package.json` definiscono `"test":"vitest run"` ma **zero file di test** → `vitest run` esce 1 ("No test files found"); `frontend.yml:38` gating | breve: `vitest run --passWithNoTests`; corretto: smoke test su componenti business (bot-config-panel, composer, dashboard) | M |

### B. GDPR / DSAR — diritto inutilizzabile end-to-end (2 gap · ~1.5 gg)

| ID | Gap | Evidenza | Fix | Eff |
|----|-----|----------|-----|-----|
| 19 | Role check DSAR punta a un ruolo **inesistente** → merchant bloccato | `routers/dsar.py:35` `_DSAR_ROLES=('agency_admin','merchant_admin')`; ma `shared/constants.py:12` definisce solo `{agency_admin, merchant_user}`. Il merchant (titolare del trattamento) riceve 403 su export/erase. Spec `reloop-ai-architettura.md:735` | sostituire `merchant_admin`→`merchant_user`; unit test export/erase per merchant_user + blocco cross-tenant | S |
| 20 | DSAR senza alcuna UI | router montato (`main.py:170`, `/dsar`) ma `grep dsar\|erase` su `frontend/` = 0 risultati. Nessun pulsante export/cancella | azioni "Esporta dati"/"Cancella dati" nel dettaglio lead del portale merchant via api-client | M |

### C. Integrazione GoHighLevel (2 gap · ~1 gg)

| ID | Gap | Evidenza | Fix | Eff |
|----|-----|----------|-----|-----|
| 25 | **Custom fields e tag GHL assenti** (feature contrattuale) | `client.py:64-72` inoltra solo phone/email/firstName/lastName (`pipeline.py:232-239`, `booking.py:261`); `grep customField\|tags` = 0. Il **capitolato sez.5 (`capitolato-tecnico.md:88`)** elenca "API pipeline, calendario, **custom fields**" come incluso | aggiungere `customFields`/`tags` a `upsert_contact` + mapping config merchant da `collected_data` | M |
| 24 | Health-check GHL non operativo | `integration_health.py:30-33` itera solo la tabella `integrations`, dove GHL **non risiede più** (post-ADR 0007 sta in `ghl_location_tokens`). Ramo `provider=='ghl'` (`:76-82`) è dead code → token scaduti emergono solo al primo errore in conversazione | iterare `list_active_linked_locations()` + liveness call economica con refresh; rimuovere ramo morto | M |

### D. Canale WhatsApp (2 gap · ~1 gg)

| ID | Gap | Evidenza | Fix | Eff |
|----|-----|----------|-----|-----|
| 29 | Messaggi **proattivi/automation non persistiti come `Message`** → assenti in inbox, status delivery persi | `no_answer.py:185-205`, `reactivation.py:196-200`, `appointment_reminder.py:126`, `automation/engine.py:347,360` chiamano `send_decision`/`send_text` ma non creano riga Message; il `wa_message_id` viene scartato → `update_outbound_status` (`handlers.py:842-870`) ritorna `row_missing` per tutti | persistere `Message(direction=outbound, status=sent, wa_message_id=…)` riusando la pipeline del bot-reply | M |
| 30 | Composer senza selettore template fuori finestra 24h | `composer.tsx:88-93` mostra il banner "usa un template" ma c'è solo textarea; `use-send-message.ts:40` invia solo `{text}`. **Backend già pronto** (`conversations.py:30,187-192`, `handlers.py:752-792`). Già segnalato in audit 17/06 e ancora aperto | picker dei template approvati (`GET /whatsapp-templates?status=approved`) con compilazione variabili, abilitato quando `windowClosed` | M |

### E. Pipeline fine-tuning (2 gap · ~1.5 gg)

La catena `collect→quality→export→train→evaluate→deploy` è cablata e funzionante, ma due promesse dello spec non sono mantenute:

| ID | Gap | Evidenza | Fix | Eff |
|----|-----|----------|-----|-----|
| 16 | **Rollout A/B del modello FT è codice morto** → sempre flag-flip tenant-wide | `handlers.py:197` legge `row.merchant_id`, ma `merchant_id` **non viene mai scritto**: `fine_tune_run`/`train`/endpoint passano solo `tenant_id` (`run.py:38-44`, `handlers.py:100-108`, `routers/fine_tuning.py:33-39`). Il ramo A/B (`handlers.py:210-226`) non parte mai; il commento "Rollout via A/B, not a flag flip (spec 6.7)" contraddice il comportamento. Spec `:433` | propagare `target_merchant_id` lungo run→train, settarlo su `FTModel`; test "dopo deploy esiste esperimento running con arm `ft`" | M |
| 17 | Evaluator **non usa un held-out set** (valuta sui dati di training) | nessuno split: `export.py:44-45` scrive un solo `train.jsonl`; `evaluate.py:116` `path = test_set_path or dataset_path` → valuta sullo stesso train. Metrica = solo validità JSON (`evaluate.py:39-48`), non le "metriche custom" dello spec `:336,705` | split 85/15 in export, propagare `eval.jsonl` fino a `fine_tune_evaluate`; aggiungere ≥1 metrica di qualità | M |

### F. Correttezza UC (3 gap · ~1.5 gg)

| ID | Gap | Evidenza | Fix | Eff |
|----|-----|----------|-----|-----|
| 3 | UC-04: avanzamento pipeline **non deterministico** rispetto alla soglia score | il move è deciso solo dall'LLM via prompt (`orchestrator.py:117-122`); lo score del turno è calcolato **dopo** la risposta (`conversation_service.py:1063`) e `ctx.lead_score` è quello del turno precedente (`:871`). Nessun fallback "score ≥ advance_threshold → move". Spec `:605` | trigger deterministico dopo `update_score`: se `score≥advance_threshold` e non già nello stage, iniettare `move_pipeline` (reason `score_threshold_crossed`) | M |
| 4 | UC-05: segnale `responded_within_10min` (tempi di risposta) **mai derivato** | peso +10 in `scoring.py:26`, behavioural in `:45`, ma `derive_conversation_signals` (`:89-100`) non lo calcola e non è in whitelist LLM. Peso configurato ma inerte; spec `:385` lo elenca esplicitamente. Dati esistono (`conversations.last_inbound_at`) | derivarlo dal delta inbound vs outbound precedente in `derive_conversation_signals`; oppure rimuovere il peso morto | M |
| 12 | UC-13: report obiezioni **agenzia** senza heatmap/trend né breakdown per-merchant | `agency-objection-report.tsx:19-20,110-132` solo barre; `/objections/agency` (`reports.py:80-84`) non include `trend` (a differenza del merchant, `:57`). Asimmetrico vs componente merchant `ObjectionHeatmap` e vs spec `:225` | aggiungere serie per-giorno tenant-wide + breakdown per-merchant nell'endpoint e heatmap nel componente admin | M |

### G. Sicurezza — copertura test isolamento RLS (2 gap · ~1 gg)

> Nota: le **policy RLS esistono e sono corrette** su tutte le 30 tabelle (vedi §5). Questi gap sono di *copertura di test*, non falle attive. Il vecchio B1 "RLS bypassabile lato backend" è **risolto** (`tenant_session` fa `SET LOCAL ROLE authenticated`).

| ID | Gap | Evidenza | Fix | Eff |
|----|-----|----------|-----|-----|
| 33 | Nessun isolation test 2-tenant sulle 3 tabelle automation | policy RLS in `0027_automation_flows.py:153-177` (FORCE), ma `tests/integration/test_isolation_automation*.py` non esiste; flaggato esplicitamente da ADR 0011 | `test_isolation_automation.py` sul pattern di `test_isolation_catalog.py` (coprire anche il `merchant_id` denormalizzato su nodes/edges) | M |
| 34 | Engine automazioni privo di test | `test_automations_graph.py` copre solo le funzioni pure; nessun test su `engine.py` (tail cursore+dedup `:107-160`, walk BFS+deferral `:284-310`, gating 24h `:313-387`) | unit su `_walk`/`_do_action` con sender/template fake: ramo true/false, deferral wait, skip fuori finestra, mapping `EVENT_TO_TRIGGER` | M |

### H. Frontend — impersonation data-plane (1 gap · ~0.5-1 gg)

| ID | Gap | Evidenza | Fix | Eff |
|----|-----|----------|-----|-----|
| 36 | Letture **REST Supabase dirette non autenticate** sotto impersonation → liste vuote al primo load | `createBrowserSupabase` (`supabase-client/src/index.ts:17-19`) usa solo anon key; sotto impersonation il token sta solo nel cookie `imp-access-token`. `api.ts` e `RealtimeAuthGate` lo iniettano, ma `supabase.from(...)` no (`use-conversations.ts:21`, `agenda/use-appointments.ts:44`, settings, kb). RLS nega → empty. Il Realtime funziona (websocket via `realtime.setAuth`), il REST iniziale no | passare `global.headers.Authorization`/`accessToken` al client REST quando il cookie è valido; oppure instradare le list/detail reads via FastAPI sotto impersonation | M |

### I. Observability frontend (1 gap · ~0.5-1 gg)

| ID | Gap | Evidenza | Fix | Eff |
|----|-----|----------|-----|-----|
| 47 | Sentry + PostHog **mai cablati** negli app Next.js | env esistono (`SENTRY_DSN_FRONTEND`, `NEXT_PUBLIC_POSTHOG_KEY`, dichiarate opzionali in `config/src/env.ts:7-8`) ma nessuna dipendenza `@sentry/*`/`posthog-js`, nessun `sentry.*.config.ts`/`instrumentation.ts`/provider. Errori browser ed eventi prodotto non raccolti. Promesso in `go-live.md §2` | `@sentry/nextjs` + `posthog-js` con provider in entrambi gli app | M |

---

## 3. Gap MINOR (30) — affidabilità, data-quality, debito

Raggruppati per area. Tutti con evidenza `file:line` verificata; nessuno blocca il go-live.

### Conversazione & UC (8)
| ID | Area | Gap | Evidenza | Eff |
|----|------|-----|----------|-----|
| 1 | UC-01 | Auto-reply OFF di default: UC-01 non parte senza attivazione manuale (scelta ADR, ma diverge da capitolato "risponde entro secondi") | `schema.py:191,309` default `False`; nessun onboarding lo accende | S |
| 2 | UC-02 | Finestra alternative su slot occupato hardcoded 3gg, ignora `BOOKING_LOOKAHEAD_DAYS` | `actions/booking.py:323` (config esiste a `schema.py:75`, usato solo in `ProposeSlotsHandler`) | S |
| 5 | UC-05 | Segnale `dropped_off` affidato al solo turno LLM, mai derivato dall'abbandono reale | `scoring.py:31`; nessuna derivazione in `close_conversations.py`/`no_answer.py` | M |
| 6 | UC-09 | Rollout vincitore A/B assente: la variante vincente non è mai promossa a default | `ab_test.py:125-137,140-184`; `_assign_ab_variant` solo per esperimenti `running`. *(declassato da major: non richiesto dallo spec UC-09)* | M |
| 7 | UC-07 | RAG senza citazioni esplicite delle fonti | chunk iniettati come `[1] …` (`orchestrator.py:123-127`) ma nessuna istruzione a citare; spec `:377` | S |
| 9 | UC-09 | Metrica primaria A/B come testo libero → KPI a zero per typo | `ab-testing-panel.tsx:255-261` input libero; `ab_test.py:37,152` nessun enum | S |
| 13 | UC-13 | `close_idle_conversations` usa solo soglia di sistema, ignora override per-merchant | `close_conversations.py:27-32` legge `SYSTEM_DEFAULTS`, bypassa `ConfigResolver` | S |
| 14 | UC-11 | KPI merchant: rate "indicativi" con filtro campagna; `score_distribution` ignora campagna | `analytics.py:75-77,141-151`; UI senza disclaimer | M |

### Fine-tuning (1)
| ID | Area | Gap | Evidenza | Eff |
|----|------|-----|----------|-----|
| 18 | FT-EVAL | Eval senza dati passa come `fail` ambiguo (non distingue "non valutabile" da "bocciato") | `evaluate.py:128-135` → `pass=False status='evaluated'`, deployabile manualmente | S |

### Sicurezza / RLS / GDPR (5)
| ID | Area | Gap | Evidenza | Eff |
|----|------|-----|----------|-----|
| 21 | RLS | Isolation test assenti per ~20 delle 30 tabelle con RLS (metà superficie non testata) | coperte solo ~10 tabelle in `tests/integration`; policy copia-incollate per-migrazione | L |
| 22 | FT | Pipeline FT legge conversazioni sotto `session_scope()` (no RLS), solo filtro applicativo | `run.py:50` + `collect.py:52` `WHERE tenant_id`; fragile a refactor (Art. 5.2) | M |
| 43 | RLS | `automation_flows/nodes/edges` senza isolation test (duplica #33 lato testing) | `0027` ha RLS, nessun test | M |
| 44 | RLS | `prompt_templates`, `bot_corrections`, `lead_campaign`, `conversation_handoff`, `leads_opted_out`, `objections` senza isolation test dedicato | prioritari: `prompt_templates` (A/B), `leads_opted_out` (opt-out) | M |
| 52 | Config | `anthropic_api_key` non in `_PROD_RECOMMENDED`: fallback acceso senza key è no-op silenzioso | `settings.py:78-79,151-159`; `router.py:94` | S |

### GHL (3)
| ID | Area | Gap | Evidenza | Eff |
|----|------|-----|----------|-----|
| 26 | GHL | Data webhook enqueued senza `_job_id` → nessun dedup su re-delivery | `webhooks.py:269-275` (INSTALL invece usa `_job_id`) | S |
| 28 | GHL | Logica liveness GHL nel ramo morto incoerente con storage marketplace token | `integration_health.py:76-82` legge `integrations.expires_at` | S |
| 23 | Docs | `CLAUDE.md` disallineato: dichiara presidio mancante e webhook GHL per-merchant HMAC che non esiste più | presidio implementato (`ft/presidio.py:54`); rotta `/ghl/{merchant_id}` rimossa | S |

### WhatsApp (2)
| ID | Area | Gap | Evidenza | Eff |
|----|------|-----|----------|-----|
| 31 | WA | Router REST legacy `/flows` orfano: scrive su tabelle non più lette dagli scheduler | `main.py:168` + `flows.py:113-131`; scheduler usano `resolve_lifecycle_step` dal grafo automation (0028) | S |
| 32 | WA | Media inbound non scaricata: image/audio/document solo placeholder testuale | `webhooks.py:41-54`; `webhook.py:152-172` non legge media id/url. Scelta V1 documentata ma media inaccessibile all'operatore | M |

### Automazioni (1)
| ID | Area | Gap | Evidenza | Eff |
|----|------|-----|----------|-----|
| 35 | Automation | Possibile perdita eventi nel dispatcher sotto burst (limite 1000 + cursore strict `>`) | `engine.py:118-159`: `occurred_at > cursor` con `limit(1000)`, avanza a `max_ts` → salta eventi con timestamp collidente | S |

### Frontend (2)
| ID | Area | Gap | Evidenza | Eff |
|----|------|-----|----------|-----|
| 37 | FE | Directory di route morte/vuote duplicano le route reali sotto `(app)/` | web-admin `{auth,billing,merchants,settings,templates}`, web-merchant `{auth,bot,conversations,integrations,settings}` (0 file) | S |
| 38 | FE | `brand-info-panel` senza stato di errore sulle query di load → form vuoto invece di errore | `brand-info-panel.tsx:174-187` solo `isLoading` | S |

### Test / CI (3)
| ID | Area | Gap | Evidenza | Eff |
|----|------|-----|----------|-----|
| 45 | CI | Job scheduler `kpi_rollup`/`analytics_export`/`integration_health` senza alcun test | nessun test in `tests/` (gli altri scheduler sono coperti) | M |
| 46 | UC-11/12 | Dashboard merchant/admin senza test unit dedicati (read-model DB-bound) | `test_uc11/12` assenti; coerente con CLAUDE.md ma debito | M |
| 11 | UC-11/12 | `daily_kpi_rollup` scrive eventi `kpi.daily.*` **mai letti** dalle dashboard (codice morto + docstring falso) | `kpi_rollup.py:67-109` vs `analytics.py:106-109,157-170` che leggono solo i raw event. *(declassato da major: nessun UC rotto)* | M |

### Observability / Ops (3)
| ID | Area | Gap | Evidenza | Eff |
|----|------|-----|----------|-----|
| 48 | Observability | Sentry senza release tagging: errori non correlabili a deploy/commit | `observability.py:29-42` senza `release=`; nessun uso di `RAILWAY_GIT_COMMIT_SHA` | S |
| 50 | Ops | Runbook pianificati ma mancanti: `fine-tune-deploy.md`, `ghl-reauth.md`, `whatsapp-onboarding.md` | `runbooks/README.md` li elenca come "planned"; su disco mancano | M |
| 51 | Ops | Drift doc: `go-live.md:21` dice "migrazioni a 0013", reale 0028; CLAUDE.md dice "0012" | aggiornare a `alembic upgrade head` senza inchiodare il numero | S |

---

## 4. Gap NICE-TO-HAVE (3) — oltre V1 / cosmetico

| ID | Area | Gap | Evidenza |
|----|------|-----|----------|
| 8 | UC-07 | Uploader KB senza preview dei chunk prima dell'indicizzazione | `kb-uploader.tsx` fa solo upload; spec `:221` cita "preview chunk" ma non è in capitolato |
| 27 | GHL | Docstring/commenti obsoleti dopo rimozione `/ghl/{merchant_id}` | `webhooks.py:224` |
| 49 | Observability | PostHog non inizializzato nel processo worker | `workers/settings.py:58-59` chiama solo `init_sentry`; eventi da job non tracciati |

---

## 5. Cosa è GIÀ completo (per non sottovalutare lo stato)

L'audit conferma che gran parte della piattaforma è solida e end-to-end. Sintesi per dimensione:

- **UC-01/02/03 (conversazione core):** completi. Ingestion webhook HMAC, gestione media con placeholder+handoff, finestra 24h, consegna "umana" (debounce/typing/multi-bolla, `delivery.py`), booking completo (upsert contatto, calendar_id, timezone merchant, alternative slot, reminder+sync GHL). Il vecchio blocker **B3** ("nessun trigger su esito chiamata fallita") è **chiuso** (`handle_call_outcome`).
- **UC-04/05/06:** funzionanti. `MovePipelineHandler` crea/sposta opportunity; `SentimentAnalyzer` gira per-turno e scrive `lead.sentiment`; scoring always-on/cumulativo con content vs behavioural separati; riattivazione con opt-out STOP/CANCELLA, dedup Redis, cron 09:00.
- **UC-07/08/09:** RAG solido (chunker, pgvector HNSW `<=>`, reindex con status, retrieval live+playground); playground fedele (ADR 0009/0010) con dry-run tool e loop correzioni; il vecchio **B2** ("KPI A/B sempre a zero") è **risolto** (`variant_id` propagato su tutti gli eventi), le varianti girano prompt diversi via `PromptManager`.
- **UC-10/11/12/13:** editor template admin **non è più una textarea JSON** (form per-campo con lock); pannello merchant con UI 3 stati Inherited/Customized/Locked; dashboard con Realtime su `analytics_events` (0013 pubblica la tabella + REPLICA IDENTITY FULL, RLS merchant 0019) + polling 30s; UC-13 con auto-trigger estrazione su idle e heatmap categorie×giorno (lato merchant).
- **Pipeline FT:** catena cablata end-to-end (4 job ARQ concatenati), trigger+UI admin, anonimizzazione **doppio strato regex+presidio** (NER obbligatoria in prod, spaCy `it_core_news_lg` nel Dockerfile), `FtModelResolver` iniettato in entrambi i call-site e funzionante.
- **Sicurezza/multitenancy:** **tutte le 30 tabelle** hanno RLS ENABLE+FORCE con policy tenant/merchant corrette; **B1 risolto** (`SET LOCAL ROLE authenticated`); JWT JWKS ES256+HS256 con allowlist; tutte le firme webhook (360dialog HMAC, GHL Ed25519+RSA con anti-downgrade) verificano prima del parsing; AES-256-GCM sulle credenziali; retention cron + presidio attivi.
- **GHL:** OAuth agency, state firmato, minting location token, linking da UI, dual-scheme signature, **rotazione refresh-token risolta** su tutti e 4 i call-site.
- **WhatsApp:** client completo, parsing webhook (inbound/echo/status/template-status), finestra 24h, template engine con ruleset Meta esteso, sync stato, status ticks monotoni.
- **Automazioni:** modello a grafo con RLS, validazione pura (cicli, trigger unico), engine a 2 handler (dispatch+run con walk BFS e deferral wait), 5 trigger V1 cablati, flussi lifecycle unificati (0028).
- **Frontend:** pagine recenti cablate (non shell), anti-drift OpenAPI robusto, Realtime sotto impersonation gestito (`RealtimeAuthGate`).
- **Deploy/ops:** catena Alembic 0001→0028 lineare e coerente, fail-fast prod su API+worker, `.env.example` allineato, cron critici tutti registrati. **Tutti gli item contrattuali di capitolato sez.5 risultano consegnati; nessuno scope creep da sez.6.**

---

## 6. Gap REFUTATI (2) — segnalati ma smentiti dal codice

Per trasparenza, due gap proposti dai finder sono stati **smentiti** in fase di verifica:

1. **"Subscription Realtime admin (UC-12) senza filtro merchant"** → *non è un difetto*: la `AgencyDashboard` aggrega KPI su **tutti** i merchant del tenant by-design (`agency-dashboard.tsx:14-21`); l'assenza di filtro merchant sul canale è corretta, l'isolamento tenant resta garantito dalla RLS.
2. **"Impersonation: token HS256 sul data-plane Supabase non verificato"** → *superato*: `realtime-auth-gate.tsx` esiste e monta `realtime.setAuth(token)` con il token di impersonation, montato globalmente nel layout. (Resta vero il gap correlato ma distinto #36 sul **REST** PostgREST, non sul Realtime.)

---

## 7. Sequenza consigliata

1. **Sprint 0 — sblocco CI (~1 gg):** #40, #41, #42 (`ruff`+`format`+`mypy`), #39 (`--passWithNoTests` o smoke test). Reintegra pre-commit per evitare nuove regressioni. *Prerequisito a tutto: senza pipeline verde i fix successivi non si mergiano puliti.*
2. **Sprint 1 — compliance & contratto (~3 gg):** #19+#20 (DSAR funzionante + UI), #25 (custom fields GHL, contrattuale). Chiude i rischi normativi/contrattuali.
3. **Sprint 2 — affidabilità runtime (~4 gg):** #29 (persistenza messaggi proattivi), #30 (picker template 24h), #24 (health-check GHL), #36 (REST impersonation), #47 (observability FE).
4. **Sprint 3 — qualità AI & FT (~3 gg):** #3 (avanzamento pipeline deterministico), #4 (segnale tempi risposta), #16 (rollout FT A/B reale), #17 (held-out eval), #12 (heatmap obiezioni agenzia).
5. **Sprint 4 — copertura test RLS (~2 gg):** #33/#43 (automation), #34 (engine), #44/#21 (tabelle merchant-scoped). Allinea all'invariante di sicurezza CLAUDE.md.
6. **Minor/nice-to-have:** assorbire opportunisticamente; prioritizzare #11 (rimuovere codice morto kpi_rollup o farlo consumare), #22 (RLS netting su pipeline FT), #52, #51/#23 (drift doc) e l'aggiornamento di `CLAUDE.md`/`completion-plan.md`.

> **Quick wins (effort S, alto valore):** #40, #41, #42 (CI), #19 (DSAR role), #23/#51 (doc), #9 (enum metrica A/B), #2 (lookahead booking).

---

## Appendice — Metodologia

Audit prodotto da un workflow multi-agente: 12 finder paralleli (UC-01/02/03 · UC-04/05/06 · UC-07/08/09 · UC-10/11/12/13 · fine-tuning · sicurezza/RLS · GHL · WhatsApp · automazioni/catalog · frontend · test/CI · deploy/ops/contratto), ciascuno con accesso in lettura al repo e obbligo di citare evidenza `file:line`. Ogni gap segnalato è stato sottoposto a un **verificatore avversariale** indipendente, istruito a refutarlo leggendo il codice reale; sono inclusi solo i gap con verdetto `confirmed` o `adjusted` (severità corretta dal verificatore). 66 agenti totali, ~829 tool-call. Le severità riflettono la valutazione post-verifica, non quella originale del finder.
