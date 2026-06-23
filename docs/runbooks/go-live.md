# Runbook — Go-live Reloop AI

Procedura completa per portare la piattaforma in produzione. Lo **stack**: Vercel/Railway non più — deploy **all-Railway** (ADR 0004) per la compute (api, worker, web-admin, web-merchant) + **Supabase Cloud EU** per dati/auth/realtime/storage. Il codice è pronto; quello che resta è **provisioning + configurazione** (questo documento) e una decisione sul debito CI.

Legenda: 🔴 bloccante · 🟡 consigliato · ⚪️ opzionale.

---

## 0. Account e prerequisiti
- 🔴 Progetto **Supabase** in regione **EU** (piano Pro per Auth hooks/Realtime).
- 🔴 Progetto **Railway**.
- 🔴 **OpenAI** API key — e verifica QUALI model id sono realmente disponibili sul tuo account (vedi §2, `LLM_MODEL_*`).
- 🔴 App **GoHighLevel Marketplace** (per l'OAuth) — §4.
- 🔴 **360dialog Partner** + il **router WhatsApp** (servizio separato, repo a parte) deployato — §5.
- 🟡 **Sentry** + **PostHog** (EU) per observability.

---

## 1. Supabase (dati / auth / storage)
1. 🔴 Crea il progetto Supabase EU. Annota: `Project URL`, `anon key`, `service_role key`, `JWT secret`, e la **connection string** del database (usa il pooler Supavisor per Railway).
2. 🔴 **Migrazioni**: girano da sole all'avvio dell'API (`api-entrypoint.sh` → `alembic upgrade head`, con advisory lock). Per eseguirle a mano: `cd backend && DATABASE/SUPABASE_DB_URL=... uv run alembic upgrade head` (attualmente arrivano a **0028**; non inchiodare il numero — usa sempre `upgrade head`). Creano: schema completo, estensione pgvector + indice HNSW, **policy RLS** su tutte le tabelle, la funzione **`public.custom_access_token_hook`** (0002), e i **bucket Storage** `kb-documents` / `ft-training-data` / `analytics-exports` con RLS (0003). → i bucket NON vanno creati a mano.
3. 🔴 **ABILITARE il Custom Access Token hook** — *il prerequisito #1*. Dashboard → **Authentication → Hooks → Custom Access Token** → punta a `public.custom_access_token_hook`. Finché è OFF, i JWT non portano `tenant_id`/`merchant_id`/`role`, **tutta la RLS vede NULL** e il portale/admin sono vuoti per tutti.
4. 🔴 **Verifica**: dopo il deploy, fai login su web-admin, decodifica il JWT (jwt.io) e conferma che contenga `tenant_id`, `merchant_id` (può essere null per l'agency_admin) e `role`. Se mancano → il hook non è abilitato.

---

## 2. Variabili d'ambiente
Il backend ha un **fail-fast** in produzione: con `ENVIRONMENT=production`, API e worker **non bootano** se manca uno dei segreti core (e logga un warning per le integrazioni opzionali).

### Backend (servizi `api` e `worker` su Railway)
**Core (🔴 obbligatori, fail-fast):**
| Var | Note |
|-----|------|
| `ENVIRONMENT` | `production` |
| `SUPABASE_URL` | Project URL |
| `SUPABASE_ANON_KEY` | |
| `SUPABASE_SERVICE_ROLE_KEY` | usato solo per op admin tracciate |
| `SUPABASE_JWT_SECRET` | per la verifica JWKS |
| `SUPABASE_DB_URL` | connection string Supabase (pooler); **non** localhost |
| `REDIS_URL` | addon Redis Railway; **non** localhost |
| `OPENAI_API_KEY` | |
| `INTEGRATIONS_KEK_BASE64` | chiave AES-256-GCM. Genera: `openssl rand -base64 32`. ⚠️ **se la perdi/cambi, bruci tutte le credenziali cifrate** (GHL/WA). Vedi `docs/runbooks/rotate-kek.md`. |

**Modelli LLM (🟡 override solo se gli id differiscono dal tuo provider):**
`LLM_MODEL_DEFAULT` · `LLM_MODEL_ESCALATION` · `LLM_MODEL_SENTIMENT` · `LLM_MODEL_FALLBACK` · `LLM_MODEL_EMBEDDING` (default rispettivamente `gpt-5-mini`, `gpt-5.2`, `gpt-5-nano`, `claude-sonnet-4-6`, `text-embedding-3-small`). **Verifica che esistano sul tuo account OpenAI**; altrimenti impostali ai nomi reali — niente più modifiche al codice.

**GHL (🔴 per il booking):** `GHL_CLIENT_ID` · `GHL_CLIENT_SECRET` · `GHL_REDIRECT_URI` (= `{PUBLIC_API_BASE_URL}/integrations/crm/oauth/callback` — il path usa `crm`, NON `ghl`, perché GHL rifiuta i redirect URI che contengono riferimenti al brand HighLevel) · `GHL_OAUTH_STATE_SECRET` (se assente usa il client_secret) · `GHL_WEBHOOK_SECRET`.

**Router/WhatsApp (🔴 per WhatsApp):** `ROUTER_BASE_URL` · `ROUTER_SHARED_SECRET` · `ROUTER_PLATFORM_ID` (default `wavemarketing`).

**URL pubblici / rete:** `PUBLIC_API_BASE_URL` · `PUBLIC_WEB_ADMIN_URL` · `PUBLIC_WEB_MERCHANT_URL` · `CORS_ALLOWED_ORIGINS` (se vuoto deriva dai due URL web) · `ALLOWED_HOSTS` (🟡 TrustedHost opt-in — **se lo imposti DEVI includere l'host dell'healthcheck Railway**, altrimenti `/health` va 400) · `RATE_LIMIT_PUBLIC_PER_MIN` (default 120).

**Observability (🟡):** `SENTRY_DSN_BACKEND` · `POSTHOG_KEY` · `POSTHOG_HOST` (default `https://eu.posthog.com`) · `LOG_LEVEL` (`info`).

**Anche per il worker (🔴):** `SUPABASE_KB_BUCKET` / `SUPABASE_FT_BUCKET` / `SUPABASE_EXPORTS_BUCKET` se diversi dai default.

### Frontend (servizi `web-admin` e `web-merchant`)
`NEXT_PUBLIC_SUPABASE_URL` · `NEXT_PUBLIC_SUPABASE_ANON_KEY` · `NEXT_PUBLIC_API_BASE_URL` · (🟡) `NEXT_PUBLIC_SENTRY_DSN` · `NEXT_PUBLIC_POSTHOG_KEY`. Senza i primi tre → 500 ad ogni richiesta.

---

## 3. Railway (compute)
Non c'è IaC: i servizi si creano a mano dalla dashboard (vedi `infra/railway/README.md`; i `.railway.toml` sono documentazione di cosa impostare nella UI).
1. 🔴 Aggiungi l'addon **Redis** → da lì ricavi `REDIS_URL`.
2. 🔴 Servizio **api** — Dockerfile `infra/docker/api.Dockerfile`, healthcheck path `/health`, env del §2, watch path `backend/**`.
3. 🔴 Servizio **worker** — Dockerfile `infra/docker/worker.Dockerfile`, **nessun** healthcheck (non-HTTP), env del §2. Il Dockerfile scarica il modello spaCy `it_core_news_lg` per la NER presidio.
4. 🔴 Servizi **web-admin** / **web-merchant** — Dockerfile `infra/docker/web.Dockerfile` con build-arg `APP_NAME=web-admin` / `web-merchant`, env `NEXT_PUBLIC_*`.
5. Le migrazioni partono al boot dell'api. Verifica nei log `alembic upgrade head` ok e `/health` verde.

---

## 4. App GHL Marketplace
1. 🔴 Crea l'app sul Marketplace GHL.
2. 🔴 **Redirect URI** = `{PUBLIC_API_BASE_URL}/integrations/crm/oauth/callback` (⚠️ usa `crm`, non `ghl`: GHL rifiuta i redirect URI che contengono "ghl"/"highlevel").
3. 🔴 **Scope** (da `oauth.py:DEFAULT_SCOPES`): `contacts.readonly`, `contacts.write`, `opportunities.readonly`, `opportunities.write`, `calendars.readonly`, `calendars.write`, `calendars/events.readonly`, `calendars/events.write`.
4. 🔴 Metti `GHL_CLIENT_ID`/`GHL_CLIENT_SECRET` in env.

---

## 5. WhatsApp (router 360dialog)
- 🔴 Il data-plane WhatsApp dipende da un **servizio router esterno** (repo separato) che fa da 360dialog Partner, fa l'embedded signup e ti notifica il canale via `POST /internal/whatsapp-connected` (firmato HMAC). Deployalo e configura `ROUTER_*`.
- ⚠️ La doc di setup del router (`NEWPLATFORM_SETUP.md`, citata nel codice) **non è in questo repo** — recuperala dal repo del router.
- Finché `ROUTER_*` non è configurato, WhatsApp è inerte (nessun inbound/outbound).

---

## 6. Bootstrap iniziale (una tantum)
1. 🔴 Sul **web-admin** registra il primo utente via Supabase Auth (signup).
2. 🔴 Il frontend chiama `POST /auth/bootstrap`: quando **non esiste alcun tenant**, crea il tenant agenzia e assegna a quell'utente i claim `agency_admin`. Idempotente; una volta seedato, l'endpoint non promuove più nessuno. (`GET /auth/bootstrap/status` dice se è ancora disponibile.)
3. 🔴 **Rilogga** (così il JWT porta i nuovi claim) e verifica `GET /auth/whoami`.

---

## 7. Onboarding per-merchant (ripetuto per ogni cliente)
1. L'agency_admin **crea il merchant** dall'admin panel.
2. **Invita** l'utente merchant (email) → imposta la password.
3. Il merchant **collega GHL** (OAuth) — ora funziona (callback fixato).
4. Il merchant inserisce **calendar_id / pipeline_id / stage IDs** nella config bot (`booking.default_calendar_id`, `pipeline.default_pipeline_id`, `pipeline.new_stage_id`, `pipeline.qualified_stage_id`). ⚠️ Oggi vanno **incollati a mano** (nessuna UI di discovery).
5. Il merchant **collega WhatsApp** (embedded signup 360dialog via router).
6. Il merchant compila il **profilo business** e **accende l'auto-reply** (`bot.auto_reply_enabled` — default **OFF**) dal pannello bot.
7. **Test E2E**: messaggio WhatsApp al numero → il bot risponde → "voglio prenotare giovedì alle 15" → appuntamento creato su GHL.

---

## 8. Smoke test di produzione
- `GET {api}/health` → `{"status":"ok"}`.
- Login → una lista RLS-protetta (es. conversazioni via Supabase) ritorna righe.
- Inbound di test → reply del bot.
- Booking via messaggio → evento su GHL Calendar.
- Log worker: cron registrati (followup_no_answer, reactivate_dormant_leads, daily_kpi_rollup, integration_health, close_idle_conversations, **enforce_retention**).

---

## 9. Decisioni di codice ancora aperte (non bloccanti)
- 🟡 **Debito CI legacy**: `ruff format --check .` rosso su ~62 file e `mypy .` ~268 errori (77 in moduli prod), pre-esistenti. Da chiudere in un **PR dedicato** (`cd backend && uv run ruff format . && uv run ruff check --fix .`, poi un giro di typing) — non in modifiche non correlate (CLAUDE.md vieta il mass-reformat opportunistico). Il deploy Railway **non** è gated da CI, quindi non blocca il go-live; tiene solo la CI rossa.
- 🟡 **`NEWPLATFORM_SETUP.md`** mancante (vedi §5).
- ⚪️ **IaC**: valuta di committare un `railway.json` per servizio per rendere il provisioning riproducibile.
- ⚪️ **TrustedHost/`/health`**: se abiliti `ALLOWED_HOSTS`, includi l'host dell'healthcheck Railway.

---

## Riferimenti
- Architettura: `reloop-ai-architettura.md`; deviazioni e stato: `CLAUDE.md`, `docs/completion-plan.md` (addendum 2026-06).
- ADR deploy: `docs/decisions/0004-all-railway-deploy.md`; pgvector: `0002-pgvector.md`.
- Runbook correlati: `rotate-kek.md`, `ecircuitbreaker-recovery.md`, `migration-rollback.md`, `supabase-restore-drill.md`.
