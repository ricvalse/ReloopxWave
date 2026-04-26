# Reloop AI — Test Plan

Everything you need to verify before treating a deploy as shippable. Organised outside-in: infra → auth → admin surface → merchant surface → conversation pipeline → security. Each check has a short "how" and an expected result; references like `api/routers/...` point into `backend/services/api/src/`.

Live URLs (update if you cut a new project):

- **API**: <https://api-production-6ac7.up.railway.app>
- **web-admin**: <https://web-admin-production-0a56.up.railway.app>
- **web-merchant**: <https://web-merchant-production.up.railway.app>

> Before a session: open Railway logs for `API` and `worker` in two terminals. Most real failures land in the logs first, UI second.

```bash
railway logs --service API
railway logs --service worker
```

---

## 0. MVP bring-up — zero → working demo

Do these once, in order. Most "I can't use anything" problems collapse into one of the checkboxes here — there's no mystery bug, just wiring that hasn't been flipped on yet.

### 0.1 Supabase Auth custom-claims hook

Without this, every login yields a JWT with no `tenant_id`/`merchant_id` claims. RLS then hides everything from the user, and the admin/merchant UIs look dead. The migration `0002_auth_jwt_hook.py` creates the function but **you must enable it manually once** in the Supabase dashboard:

- [ ] Supabase dashboard → **Authentication → Hooks → Custom Access Token** → **Enable**
- [ ] Pick function: `public.custom_access_token_hook`
- [ ] Save, then log out + log back in of the admin app (JWTs are issued at login)
- [ ] Verify: `curl -H "Authorization: Bearer <jwt>" $API/auth/whoami` returns a non-null `tenant_id`. Or decode the JWT at jwt.io and check the top-level claims.

### 0.2 Env vars on Railway (shared across api + worker)

```
# Core
SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_JWT_SECRET
SUPABASE_DB_URL   # Supavisor pooler, port 5432 — see infra/railway/README.md
REDIS_URL=${{Redis.REDIS_URL}}
OPENAI_API_KEY
INTEGRATIONS_KEK_BASE64   # generate once; losing it breaks every stored secret
PUBLIC_API_BASE_URL, PUBLIC_WEB_MERCHANT_URL, PUBLIC_WEB_ADMIN_URL
CORS_ALLOWED_ORIGINS

# WhatsApp (360dialog — single Partner channel for the whole platform)
WHATSAPP_D360_API_KEY            # Partner API key from 360dialog hub
WHATSAPP_D360_WEBHOOK_SECRET     # HMAC secret configured in the 360dialog portal

# GHL
GHL_CLIENT_ID, GHL_CLIENT_SECRET, GHL_WEBHOOK_SECRET
GHL_REDIRECT_URI=https://<api-domain>/integrations/ghl/oauth/callback
```

### 0.3 Configure the 360dialog Partner

Wave Marketing operates a single 360dialog Partner account; every merchant's
WhatsApp number is a *channel* under that one partnership. There's no
per-merchant API key — the `WHATSAPP_D360_API_KEY` env var is shared across
the whole deployment.

- [ ] Create / log in to your 360dialog Partner Hub. Note the Partner API key.
- [ ] Onboard each merchant's number as a channel under your Partner account.
      Each channel exposes a `phone_number_id` (same identifier Meta uses).
- [ ] Set the webhook URL on the Partner Hub:
      `https://<api-domain>/webhooks/whatsapp/<phone_number_id>`
- [ ] (Recommended) Set an HMAC signing secret in the 360dialog portal and
      mirror the same value into `WHATSAPP_D360_WEBHOOK_SECRET` on Railway —
      the route rejects unsigned posts when the env var is set.
- [ ] Set `WHATSAPP_D360_API_KEY` on Railway and redeploy.

### 0.4 Walk through the first merchant

On a fresh Supabase, there is nothing in `tenants`, `merchants`, `users`, `bot_configs`. The first signup on `web-admin` auto-bootstraps to `agency_admin`. From there:

- [ ] Admin signs up at `$WEB_ADMIN/login`, lands on `/dashboard`.
- [ ] Admin creates a tenant via `/tenants/new` (if not auto-seeded).
- [ ] Admin creates a merchant under the tenant from `/merchants/new`.
- [ ] Admin invites a merchant-user at `/users/invite`; user accepts and signs in at `$WEB_MERCHANT/login`.
- [ ] Merchant dashboard shows the "Pronto per partire" checklist. Walk it top-to-bottom:
  - [ ] Collega WhatsApp — paste the channel's `phone_number_id` (no API key — that's platform-wide)
  - [ ] Collega GoHighLevel (OAuth)
  - [ ] Compila il profilo attività (`/bot/config` → "Profilo attività")
  - [ ] Imposta `booking.default_calendar_id` (from GHL) — until this is set, `book_slot` silently fails
  - [ ] Imposta `pipeline.qualified_stage_id` (from GHL) — until this is set, `move_pipeline` silently fails
  - [ ] (Optional) Carica un PDF nella Knowledge Base
- [ ] Send a WhatsApp message to the connected number from your phone → within ~10s the bot replies, a conversation appears under `/conversations`, and `/dashboard` KPIs tick up in realtime.

### 0.5 Verify scheduled jobs are firing

Follow-ups (UC-03), reactivation (UC-06), daily KPI rollup and integration health check are registered as in-process ARQ cron jobs (see `workers/settings.py` — `cron_jobs`). No separate Railway Cron service is required.

- [ ] `railway logs --service worker | grep cron` shows `scheduled <name>` lines within a few minutes of boot.
- [ ] `followup_no_answer` runs every 15 min; `reactivate_dormant_leads` daily at 09:00 UTC; `daily_kpi_rollup` daily at 00:15 UTC; `integration_health_check` every 4 h.

---

## 1. Infra smoke (do these first, ~60s)

- [ ] `curl $API/health` → `{"status":"ok","environment":"production"}`
- [ ] `curl -I $WEB_ADMIN/login` → `200 OK`
- [ ] `curl -I $WEB_MERCHANT/login` → `200 OK`
- [ ] Railway dashboard: `API`, `worker`, `web-admin`, `web-merchant`, `Redis` all showing latest deployment **SUCCESS**
- [ ] `railway logs --service worker | grep "Starting worker for"` shows **9 functions** registered: `handle_inbound_message`, `followup_no_answer`, `reactivate_dormant_leads`, `daily_kpi_rollup`, `objection_extraction`, `kb_reindex`, `fine_tune_train`, `fine_tune_evaluate`, `fine_tune_deploy`
- [ ] `railway logs --service Redis | tail` shows periodic RDB `Background saving terminated with success`

## 2. Auth + tenant bootstrap (UC-00-ish — not an official UC, but nothing else works without it)

The first ever login bootstraps an agency-admin. Keep the two JWTs (admin + merchant user) around for the rest of the checks.

- [ ] Open `$WEB_ADMIN/login`, sign up with `dev@amaliatech.ai`. Supabase sends a magic link → click it → land on `/dashboard`.
- [ ] `GET /auth/whoami` with the admin JWT returns `{ "tenant_id": "...", "role": "agency_admin", ... }`
- [ ] Admin can create a tenant (if the seeded one doesn't exist): `POST /tenants/` returns 201.
- [ ] Admin creates a merchant under the tenant: `POST /merchants/` with body `{ "name": "Test Merchant", "tenant_id": "<id>" }` returns 201.
- [ ] Admin invites a merchant-user: `POST /users/invite` with body `{ "email": "merchant@test.local", "merchant_id": "<id>", "role": "merchant_admin" }` → 200, invite email arrives.
- [ ] Merchant user accepts invite, logs in at `$WEB_MERCHANT/login`, lands on `/dashboard`.

## 3. Admin surface — agency panel (`web-admin`)

### 3.1 UC-12 Dashboard Unificata Admin Agenzia — `/dashboard`

- [ ] Page renders with KPI cards. With zero merchants, numbers are 0 or `—`, no crashes.
- [ ] Supabase Realtime subscription: open the DevTools console on the dashboard, confirm a `WebSocket` connection to `wss://<ref>.supabase.co/realtime/v1/websocket` is open.
- [ ] `GET /analytics/agency/kpis` with admin JWT returns the same numbers the UI shows. No tenant_id header needed — read from JWT claim.
- [ ] Force a KPI update: trigger the scheduler job `daily_kpi_rollup` (ARQ queue `scheduler:jobs`) and watch the number change within ~1s on the dashboard without a refresh.

### 3.2 UC-10 Bot Template Agenzia — `/templates`

- [ ] Page lists templates (empty state OK on fresh project).
- [ ] Create a template: fill form → `POST /bot-config/templates` → 201, appears in list.
- [ ] Edit the template's `defaults` JSONB via the form; verify `GET /bot-config/templates/{id}` reflects the new values.
- [ ] On a merchant without its own overrides, `GET /bot-config/{merchant_id}/resolved` returns the agency template's values (second level of the three-level cascade).

### 3.3 Merchants list + detail — `/merchants`, `/merchants/[id]`

- [ ] `/merchants` lists every merchant under the admin's tenant.
- [ ] Cross-tenant isolation: create a second tenant via `POST /tenants/`, log in as its admin, confirm `/merchants` does **not** show the first tenant's merchants. (This is the RLS smoke test — see §7.)
- [ ] Merchant detail page loads `GET /merchants/{id}`.
- [ ] Suspend a merchant: `POST /merchants/{id}/suspend` → `status="suspended"`; the merchant user's next request to `$WEB_MERCHANT` gets 403.

## 4. Merchant surface — merchant portal (`web-merchant`)

### 4.1 UC-11 Dashboard Analytics Merchant — `/dashboard`

- [ ] KPI cards render.
- [ ] `GET /analytics/merchant/kpis?merchant_id=<id>` matches.
- [ ] Realtime: insert a row into `analytics_events` for this merchant (via Supabase SQL editor or a conversation turn) → number updates live.

### 4.2 UC-08 Playground — `/bot/playground`

- [ ] Page loads with a chat UI.
- [ ] Send a message → `POST /playground/turn` → returns `{ reply, actions, tokens }`. Reply arrives in <5s.
- [ ] Playground uses the merchant's **current** bot config, not agency defaults — verify by editing `/bot/config`'s `system_prompt_additions` and sending a new playground message that references the new text.
- [ ] Playground does **not** persist to `conversations` table (it's a sandbox). Check: `SELECT count(*) FROM conversations WHERE merchant_id = <id>` is unchanged after playground use.

### 4.3 UC-07 Knowledge Base — `/bot/knowledge-base`

- [ ] Upload a PDF via the UI. Upload goes directly to Supabase Storage (browser → Storage), not through the API.
- [ ] `POST /knowledge-base/{merchant_id}/docs` is called once the upload finishes, returns `{ id, status: "indexing" }`.
- [ ] Worker picks up `kb_reindex` job; within ~30s, doc row transitions to `status: "ready"` and `kb_chunks` rows appear with non-null `embedding` (pgvector).
- [ ] Send a playground message referencing a fact from the PDF; reply should cite it. If not, check `rag.top_k` (default 5) and `rag.min_score` (default 0.7) in the resolved config.
- [ ] Re-index: `POST /knowledge-base/{merchant_id}/docs/{doc_id}/reindex` → 202, re-embeds chunks.

### 4.4 UC-09 A/B Testing — `/bot/ab-testing`

- [ ] Create an experiment: `POST /ab-test/` with `{ control_config_id, variant_config_id, split: [50,50] }` → 201.
- [ ] Start the experiment: `POST /ab-test/{id}/start` → `status="running"`.
- [ ] Send 10 playground messages; `GET /ab-test/{id}/metrics` shows assignments roughly 5/5 between variants.
- [ ] Verify the assignment is **sticky per conversation**: same `conversation_id` across turns always hits the same variant (check `ab_assignments` table).

### 4.5 UC-13 Report Obiezioni — `/reports/objections`

- [ ] Page loads. If no conversations have been processed yet, shows `Nessuna obiezione classificata negli ultimi 30 giorni.`
- [ ] Trigger manual extraction on one conversation: `POST /reports/objections/extract/{conversation_id}` → 202 (enqueues `objection_extraction`).
- [ ] Worker finishes; `GET /reports/objections?since_days=30` returns categorised counts + sample quotes.
- [ ] UI shows category bars. The bar widths were the bug fixed pre-deploy (type error on empty data) — verify an empty merchant still renders without throwing.

### 4.6 Conversations viewer — `/conversations`

- [ ] Empty state renders for a fresh merchant.
- [ ] After feeding one WhatsApp message (§6), the list shows one conversation with latest message preview, pipeline stage, and score.
- [ ] Click into detail: `/conversations/{id}` streams messages via Supabase Realtime; new inbound/outbound messages appear without refresh.

### 4.7 Integrations — `/integrations`

- [ ] Status card shows `WhatsApp: not connected`, `GHL: not connected` on a fresh merchant.
- [ ] `GET /integrations/status?merchant_id=<id>` returns the same.
- [ ] **GHL OAuth**: click "Connect GHL" → redirected to `GET /integrations/ghl/oauth/start` → GHL consent page → callback to `/integrations/ghl/oauth/callback` → back to `/integrations` with status `connected`. (Needs real GHL client credentials in Railway env.)
- [ ] **WhatsApp**: paste phone-number-id + system user token → `POST /integrations/whatsapp/verify` → status `connected`. The verify token webhook challenge (`GET /webhooks/whatsapp/{phone_number_id}`) has to resolve against Meta's setup flow.

## 5. Scheduled jobs (ARQ, `workers/scheduler`)

All scheduled jobs live on `scheduler:jobs` queue; you can trigger any of them on demand via:

```bash
railway run --service worker python -c "import asyncio; from workers.scheduler.<name> import <func>; asyncio.run(<func>(<ctx>))"
```

- [ ] **UC-03 `followup_no_answer`**: create a conversation with a merchant-side send timestamp older than `no_answer.first_reminder_min` (default 120 min) and no inbound since → job sends a follow-up via WhatsApp. Max 2 follow-ups (default).
- [ ] **UC-06 `reactivate_dormant_leads`**: seed a lead with `last_contact_at` > `reactivation.dormant_days` ago (default 90) → job sends a re-engagement message.
- [ ] **`daily_kpi_rollup`**: rollup writes aggregate rows to `analytics_events`; admin dashboard KPIs update.
- [ ] **`objection_extraction`**: fires on conversation close → extracts objections into `objections` table.
- [ ] **`kb_reindex`**: fires on KB doc upload / reindex request; re-embeds chunks.
- [ ] **`fine_tune_train` / `evaluate` / `deploy`** (weeks 9–10 features — skip until the pipeline is built out).

## 6. Conversation pipeline (UC-01, 02, 04, 05)

Needs a connected WhatsApp sandbox + a connected GHL merchant. These are the bet-the-company flows; do not ship without running them.

- [ ] **Webhook signature**: `POST /webhooks/whatsapp/<phone_number_id>` with a body but a **wrong** `X-Hub-Signature-256` → 401 and nothing in the queue. Check `integrations/whatsapp/signatures.py`.
- [ ] **UC-01 First Response**: send an inbound WhatsApp message from a new number → worker picks up `handle_inbound_message`, calls Orchestrator, sends a reply within the target SLA (target: p50 < 10s per section 1 of the spec). Check `conversations` + `messages` tables for one row each.
- [ ] **UC-02 Booking**: lead replies affirmatively to a booking prompt; orchestrator emits `action: book_slot` → GHL Calendar receives a booking. Verify the booking appears in GHL and the conversation records `booking_id` in metadata.
- [ ] **UC-04 Pipeline auto-move**: after a qualified exchange, `OpportunityService.move_pipeline()` transitions the GHL opportunity to `pipeline.qualified_stage_id`. Verify in GHL UI.
- [ ] **UC-05 Lead Scoring**: score is written to `leads.score` after each turn. Hot lead (`score >= scoring.hot_threshold`, default 80) triggers escalation — orchestrator routes to `gpt-5.2` per `ModelRouter` rules in `libs/ai_core/model_router.py`.

## 7. Security & multitenancy (do NOT skip these)

These are the "destroys the product if wrong" checks. Section 15 of the architecture doc commits to CI coverage; these are the manual equivalents.

- [ ] **RLS isolation — Postgres-level**: with the Supabase SQL editor, run `SET LOCAL "request.jwt.claims" = '{"tenant_id":"<A>","role":"agency_admin"}'` then `SELECT * FROM merchants WHERE tenant_id = '<B>'`. Expected: zero rows (not an error — RLS filters silently).
- [ ] **RLS isolation — API-level**: two admin JWTs for different tenants. Admin A calls `GET /merchants/{id}` with an id belonging to tenant B → **404** (not 403, not the record). This is the "does Supabase RLS cover what the API forgot to check" test.
- [ ] **Service role gating**: there must be **no** API endpoint that uses the Supabase service role without an `actor_id` log line. `grep -R "service_role\|session_scope" backend/services/api/src` should show only admin-endpoint uses, each accompanied by a `log.info(..., actor_id=...)`.
- [ ] **KEK present**: `railway variables --service API | grep INTEGRATIONS_KEK_BASE64` returns a 44-char base64 value. Losing this breaks every encrypted GHL / WhatsApp secret.
- [ ] **Encrypted at rest**: `SELECT ciphertext FROM integrations LIMIT 1` returns bytes, not plaintext (the plaintext-looking value = bug).
- [ ] **Webhook HMAC**: as §6 first bullet — unsigned or wrong-signature WhatsApp webhook → 401, dropped.
- [ ] **Auth hook claims**: a fresh JWT decoded at <https://jwt.io> includes `tenant_id`, `merchant_id` (if role=merchant_*), and `role` at the top level (not under `user_metadata`).
- [ ] **Anonymisation before FT**: any dataset exported for OpenAI fine-tuning must pass `data_anonymizer` (presidio + regex). Check `workers/fine_tuning/export.py` writes through the anonymiser. This is contractual (architettura §art. 5.2), not optional.

## 8. Config resolution (three-level cascade, section 9 of the doc)

- [ ] **System defaults only**: on a merchant with no overrides and a fresh agency (no template), `GET /bot-config/{merchant_id}/resolved` returns all system defaults from `libs/config_resolver/schema.py`.
- [ ] **Agency overrides system**: create a template, set `rag.top_k=7` — resolved endpoint returns 7.
- [ ] **Merchant overrides agency**: `PUT /bot-config/{merchant_id}/overrides` with `{ "rag": { "top_k": 3 } }` — resolved endpoint returns 3.
- [ ] **Redis caching (~60s TTL)**: after an override change, the resolved endpoint reflects it within ~1s (cache invalidation on write, not a TTL expiry — confirm `config_resolver/cache.py`'s `invalidate` is called on the PUT).
- [ ] **Invalid schema rejected**: `PUT /bot-config/{merchant_id}/overrides` with `{ "rag": { "top_k": "seven" } }` → 422. (Pydantic validation must hold.)

## 9. Operational hygiene

- [ ] `./scripts/generate-api-types.sh` against the live API produces no diff against `frontend/packages/api-client/src/generated.ts`. (CI also enforces this.)
- [ ] `cd backend && uv run pytest` passes.
- [ ] `cd backend && uv run ruff check . && uv run mypy .` clean.
- [ ] `cd frontend && pnpm lint && pnpm typecheck` clean.
- [ ] `alembic current` on the API matches the latest file in `backend/libs/db/src/db/migrations/versions/`.

## 10. Things that don't exist yet (explicitly)

So you don't chase ghosts:

- Fine-tuning pipeline (UC-01..05 improvement) is weeks 9–10 work — workers handlers exist as stubs, the FT model is not yet per-tenant.
- No Sentry / PostHog wired until you set the DSNs in Railway (`SENTRY_DSN_BACKEND`, `NEXT_PUBLIC_SENTRY_DSN`, `POSTHOG_KEY`, `NEXT_PUBLIC_POSTHOG_KEY`).
- No preview environments. A second Railway project for staging (as §13.1 suggests) is the next infra step.
- The architecture doc (`reloop-ai-architettura.md`) still references Vercel for frontend; we're currently all-Railway. `infra/railway/README.md` has the ADR-ish note.
