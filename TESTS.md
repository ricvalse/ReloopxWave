# Reloop AI — Test plan

A runbook for testing the platform end-to-end. Sections build on each other —
do them in order the first time, then jump straight to §6 for re-tests once
everything is wired.

Live environment:

- **API**: <https://api-production-6ac7.up.railway.app>
- **web-admin** (Wave Marketing operators): <https://web-admin-production-0a56.up.railway.app>
- **web-merchant** (each merchant's portal): <https://web-merchant-production.up.railway.app>

Open Railway logs in two terminals before starting — most failures show up there
first, the UI second:

```bash
railway logs --service API
railway logs --service worker
```

---

## 1. External accounts you need

The platform calls into three external services. Get these set up before any of
the conversation use cases (UC-01..06, 13) can be tested.

### 1.1 OpenAI

- Create / log in to OpenAI Platform → Settings → API Keys → **Create new key**.
- Copy the `sk-...` value. Required for: orchestrator, model router, KB
  embeddings, objection classifier.

### 1.2 360dialog (single Partner — shared across all merchants)

Wave Marketing operates as **one** 360dialog Partner; every merchant's WhatsApp
number is a channel under that one partnership.

- Create / log in to your 360dialog Partner Hub.
- Generate a **Partner API key**.
- Onboard at least one WhatsApp channel. Each channel exposes a
  `phone_number_id` (the same identifier Meta uses).
- In the channel's webhook config, set:
  - **URL**: `https://api-production-6ac7.up.railway.app/webhooks/whatsapp/<phone_number_id>`
  - **HMAC signing secret** — pick any strong random string and write it down.
  - Subscribe to `messages` events.

### 1.3 GoHighLevel (per merchant install)

- Create a GHL **Marketplace app** (or use an existing one).
- Add the redirect URI: `https://api-production-6ac7.up.railway.app/integrations/ghl/oauth/callback`
- Note the **client_id** and **client_secret**.
- Generate a **webhook signing secret** (any strong random string).

---

## 2. Railway env vars

Set these on **both** the `API` and `worker` services. After each change Railway
auto-redeploys.

```
# AI
OPENAI_API_KEY=sk-...

# WhatsApp (360dialog — single Partner channel for the whole platform)
WHATSAPP_D360_API_KEY=<Partner API key from 1.2>
WHATSAPP_D360_WEBHOOK_SECRET=<HMAC secret from 1.2>

# GHL
GHL_CLIENT_ID=<from 1.3>
GHL_CLIENT_SECRET=<from 1.3>
GHL_WEBHOOK_SECRET=<from 1.3>
GHL_REDIRECT_URI=https://api-production-6ac7.up.railway.app/integrations/ghl/oauth/callback
```

These should already be set (don't recreate or you'll lose data):

```
SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_JWT_SECRET
SUPABASE_DB_URL
REDIS_URL=${{Redis.REDIS_URL}}
INTEGRATIONS_KEK_BASE64       # losing this bricks every encrypted secret
PUBLIC_API_BASE_URL, PUBLIC_WEB_ADMIN_URL, PUBLIC_WEB_MERCHANT_URL
CORS_ALLOWED_ORIGINS
```

Quick verification:

```bash
curl https://api-production-6ac7.up.railway.app/health
# → {"status":"ok","environment":"production"}

curl https://api-production-6ac7.up.railway.app/auth/bootstrap/status
# → {"available":false,"tenant_id":"<uuid>"} once you've signed in once as admin
# → {"available":true,"tenant_id":null} on a brand new deploy
```

---

## 3. One-time Supabase setup

The Auth hook injects `tenant_id`, `merchant_id`, and `role` into every JWT.
Without it every API call returns `403 missing_tenant_claim`.

- Open Supabase dashboard → project `izhyypbjeqkqdxfnzzoo`.
- **Authentication → Hooks → Custom Access Token hook → Add hook**.
- Hook type: **Postgres**, schema `public`, function `custom_access_token_hook`.
- **Enable** and save.

(If you've already done this, skip — no-op.)

---

## 4. Bootstrap the Wave Marketing admin

First admin only. After this, additional admins should be invited rather than
bootstrapped.

- Sign up at `web-admin/login` with the address you want as agency_admin
  (e.g. `admin@relooptech.ai`).
- The login form auto-calls `POST /auth/bootstrap` after the password lands —
  on a fresh deploy this creates the Wave Marketing tenant, promotes you to
  `agency_admin`, and forces a session refresh.
- You should land on `/dashboard` with no console errors.
- Verify the JWT carries the right claims:

```bash
curl -H "Authorization: Bearer <jwt>" \
     https://api-production-6ac7.up.railway.app/auth/whoami
# → {"actor_id":"...","tenant_id":"...","role":"agency_admin","merchant_id":null}
```

If `whoami` returns 403 with `missing_tenant_claim`, the Auth hook isn't on
yet (revisit §3). Sign out and back in to mint a fresh JWT.

---

## 5. Onboard one test merchant

Repeat for every merchant you want to test with. ~5 minutes each.

### 5.1 Create the merchant

- web-admin → **Merchant** → **+ Nuovo merchant**.
- Fill name + slug.
- The merchant appears in the list. Click it to land on `/merchants/[id]`.

### 5.2 Invite a merchant user

- On the merchant detail page, scroll to **Utenti merchant** → **+ Invita utente**.
- Email + optional full_name → **Invia invito**.
- Supabase sends a magic link. The recipient clicks it → lands on
  `web-merchant/login` already authenticated.

The merchant_user has RLS scoped to this merchant only.

### 5.3 Connect WhatsApp (per merchant)

On the merchant portal (signed in as the invited user):

- **Integrazioni → Collega WhatsApp**.
- Paste the channel's `phone_number_id` from your 360dialog Partner Hub.
- No API key field — that's platform-wide.
- **Verifica e salva** → the API pings 360dialog with the platform key, persists
  the merchant ↔ channel mapping.

### 5.4 Connect GHL (per merchant)

- **Integrazioni → Collega GoHighLevel** → opens GHL OAuth consent.
- Approve → redirected back to `web-merchant/integrations` with
  `Connesso correttamente`.

### 5.5 Fill the merchant config

`web-merchant → Configurazione bot`:

- **Profilo attività** — name, industry, description, offer, hours, location,
  pricing notes, website. The system prompt is generated from these.
- **Bot** — language (`it`), tone, optional `system_prompt_additions`,
  optional `first_message`.
- **Booking (UC-02)** — paste your GHL `default_calendar_id`.
- **Pipeline (UC-04)** — paste GHL `default_pipeline_id`, `new_stage_id`,
  `qualified_stage_id`.
- **Scoring (UC-05)** — leave defaults (`hot_threshold=80`, `cold=30`) or tune.

The merchant dashboard's "Pronto per partire" checklist surfaces each missing
field as an action — once it disappears, you're set up.

### 5.6 (Optional) Upload a knowledge base doc

`web-merchant → Knowledge base → upload PDF/docx/url`. The worker indexes
within ~30s; status flips to `ready`. Used by UC-07.

---

## 6. Test each use case

UCs grouped by dependency. Within each group the order doesn't matter.

### Group A — no external dependencies

These work the moment OpenAI + the platform are up. Perfect for a smoke test.

#### UC-08 Playground

- web-merchant → **Playground**.
- Type a message → the bot replies in <5s.
- Verify the system prompt reflects your business profile: ask
  *"Cosa fate?"* → reply should mention your business name / industry / offer.
- Edit the **Profilo attività** and resend → next playground turn picks up
  the new prompt within ~60s (Redis cache TTL).
- Confirm the playground does **not** persist to `conversations`:
  ```sql
  select count(*) from conversations where merchant_id = '<id>';  -- unchanged
  ```

#### UC-10 Bot template (admin)

- web-admin → **Template bot → Nuovo**.
- Set `defaults` overrides (e.g. `rag.top_k=7`).
- Mark it **default** for the tenant.
- On a merchant with no overrides:
  ```bash
  curl -H "Authorization: Bearer <admin-jwt>" \
       "https://api-production-6ac7.up.railway.app/bot-config/<merchant_id>/resolved"
  # → reflects the template's value
  ```

#### UC-11 / UC-12 dashboards

- web-merchant `/dashboard` and web-admin `/dashboard` render with KPIs at 0.
- After UC-01 traffic, KPIs tick up live (Supabase Realtime subscription).

### Group B — needs OpenAI only

#### UC-07 Knowledge base + RAG

Prereq: 5.6 (uploaded doc).

- Wait until `kb_docs.status = 'ready'`.
- In **Playground**, ask a question whose answer is in the PDF.
- Reply should cite the doc; check the playground response's
  `retrieved_chunks` array (visible in network panel).
- If retrieval misses, lower `rag.min_score` (default 0.7) in `/bot/config`.

### Group C — full WhatsApp pipeline

Prereq: 5.3 (WhatsApp connected). All require sending real messages.

#### UC-01 First response

- From your phone, message the merchant's WhatsApp number.
- Within ~10s the bot replies.
- web-admin **Inbox** shows the conversation (filtered by merchant or "tutti").
- web-merchant **Conversazioni** shows the same thread; messages stream live.
- DB sanity check:
  ```sql
  select count(*) from conversations where merchant_id = '<id>';  -- 1
  select role, content from messages where conversation_id = '<id>' order by created_at;
  ```

#### UC-05 Lead scoring

Automatic each turn. Drive a few back-and-forths providing name, email, intent.

```sql
select score, score_reasons from leads where phone = '<your-phone>';
-- score > 50 after a couple of turns; reasons list signals like
-- 'has_name', 'has_email', 'asked_for_booking', etc.
```

#### UC-13 Objections

- Drive a conversation that contains an objection ("è troppo caro",
  "non mi fido", etc.).
- Manually trigger extraction (V1 doesn't run on every close yet):
  ```bash
  curl -X POST -H "Authorization: Bearer <jwt>" \
    https://api-production-6ac7.up.railway.app/reports/objections/extract/<conversation_id>
  ```
- web-merchant → **Reports → Obiezioni** shows category buckets + sample quotes.

#### UC-09 A/B testing

- web-merchant → **A/B testing → Nuovo esperimento**.
- Two variants (`control_config_id`, `variant_config_id`), 50/50 split → **Avvia**.
- Drive 10 conversations.
- Page metrics show ~5/5 split; `ab_assignments` table proves stickiness
  (same lead → same variant across turns).

### Group D — needs WhatsApp + GHL

Prereqs: 5.3 + 5.4 + 5.5.

#### UC-02 Booking

- In an active conversation, say "vorrei prenotare lunedì alle 10".
- Bot emits `book_slot` action.
- Backend:
  - Upserts the GHL contact for the lead.
  - Ensures a GHL opportunity exists (creates one in
    `pipeline.default_pipeline_id` at `pipeline.new_stage_id` if not).
  - Books the calendar slot.
  - Sends a confirmation WhatsApp message.
- Verify in GHL: contact exists, opportunity exists, calendar event exists.
- Verify on `leads.meta`:
  ```sql
  select meta from leads where phone = '<your-phone>';
  -- → {"ghl_opportunity_id":"...","ghl_pipeline_id":"..."}
  ```

#### UC-04 Pipeline move

Prereq: UC-02 ran (so `lead.meta` carries the opportunity_id).

- In the conversation, say something that signals qualification ("ok confermo",
  "sì sono interessato a iniziare").
- Bot emits `move_pipeline`.
- Handler reads opportunity_id + pipeline_id from `lead.meta`, moves the
  opportunity to `pipeline.qualified_stage_id` in GHL.
- Verify in GHL UI: opportunity moved.
- Analytics:
  ```sql
  select event_type, properties from analytics_events
  where event_type in ('pipeline.moved','pipeline.failed')
  order by occurred_at desc limit 5;
  ```

### Group E — scheduler

ARQ cron jobs run in-process on the worker (see `workers/settings.py`).
Default schedules:

| Job | Cadence (UTC) |
|---|---|
| `followup_no_answer` (UC-03) | every 15 min |
| `reactivate_dormant_leads` (UC-06) | daily 09:00 |
| `daily_kpi_rollup` | daily 00:15 |
| `integration_health_check` | every 4 h |

Worker boot log line confirms: `Starting worker for 12 functions: ...`.

#### UC-03 Follow-up

- Drop `no_answer.first_reminder_min` to `5` in `/bot/config` so you don't
  have to wait two hours.
- Send one inbound message; let the bot reply. Stop replying.
- Wait ~5 min plus the cron tick.
- A reminder should arrive on your phone (`"Ciao! Eri ancora interessato?..."`).
- Restore the threshold afterwards.

To trigger on demand without waiting:

```bash
railway run --service worker python -c "
import asyncio
from workers.scheduler.no_answer import followup_no_answer
asyncio.run(followup_no_answer({'settings': __import__('shared').get_settings()}))
"
```

#### UC-06 Reactivation

- Backdate a lead in SQL editor:
  ```sql
  update leads set meta = jsonb_set(coalesce(meta,'{}'::jsonb),
                                    '{last_reactivation_at}', 'null'::jsonb)
   where phone = '<your-phone>';
  update conversations set last_message_at = now() - interval '95 days'
   where lead_id = (select id from leads where phone = '<your-phone>');
  ```
- Manually trigger:
  ```bash
  railway run --service worker python -c "
  import asyncio
  from workers.scheduler.reactivation import reactivate_dormant_leads
  asyncio.run(reactivate_dormant_leads({'settings': __import__('shared').get_settings()}))
  "
  ```
- A reactivation message hits your phone.

#### `daily_kpi_rollup`

- Trigger on demand:
  ```bash
  railway run --service worker python -c "
  import asyncio
  from workers.scheduler.kpi_rollup import daily_kpi_rollup
  asyncio.run(daily_kpi_rollup({}))
  "
  ```
- Admin and merchant dashboards reflect new aggregates.

---

## 7. Multi-tenant + RLS smoke

All tables enforce RLS scoped on `tenant_id` / `merchant_id`. Quick checks:

- web-admin sees every merchant's conversations under `/inbox`.
- web-merchant only sees its own. Sign in as a second merchant_user (different
  merchant) → its `/conversations` is empty even if the first merchant's
  inbox is full.
- API-level: as a merchant_user, `GET /merchants/<other-merchant-id>` returns
  **404** (not 403 — RLS hides the row, the handler surfaces it as not found).

Encryption sanity:

```sql
select ciphertext from integrations limit 1;
-- → bytea bytes, never plaintext. Plaintext = bug.
```

---

## 8. Webhook signature checks

WhatsApp inbound:

```bash
# Wrong signature → 401, no DB writes
curl -i -X POST \
  -H "X-Hub-Signature-256: sha256=deadbeef" \
  -H "Content-Type: application/json" \
  -d '{"foo":"bar"}' \
  https://api-production-6ac7.up.railway.app/webhooks/whatsapp/<phone_number_id>
# → HTTP/2 401
```

GHL inbound: same shape with `X-GoHighLevel-Signature`.

---

## 9. Operational hygiene

Run before treating a deploy as shippable.

- [ ] `cd backend && uv run ruff check .` — clean.
- [ ] `cd backend && uv run pytest` — 66/66 unit tests pass.
- [ ] `cd frontend && pnpm lint && pnpm typecheck` — both green.
- [ ] Railway: API + worker + web-admin + web-merchant + Redis all show
      latest deploy as **SUCCESS**.
- [ ] `railway logs --service worker | grep "Starting worker for"` lists 12
      functions registered (8 scheduler + conversation + GHL event +
      3 fine-tuning stubs).
- [ ] Supabase dashboard → Database → Migrations → latest = `0006_drop_super_admin`.

---

## 10. Known limitations

Things that work but with caveats — flag them on demos so nobody is surprised.

- **Inbound from unknown numbers**: the platform routes inbound by
  `phone_number_id` (channel) → merchant. The "from" phone of a brand-new
  customer is unknown until they reply once; first response always works,
  follow-up scheduling assumes the lead row got created on first reply.
- **UC-04 needs UC-02 first**: `move_pipeline` reads `ghl_opportunity_id`
  off `leads.meta`, which is stamped by the booking handler. If the bot
  never books a slot for a lead, no opportunity exists and `move_pipeline`
  returns `opportunity_required`. Workaround: configure
  `pipeline.default_pipeline_id` + `new_stage_id` so UC-02 always creates
  one, even when the booking itself fails (e.g. slot unavailable).
- **Fine-tuning (UC-09 tie-in, weeks 9–10)**: the workers `fine_tune_train`,
  `fine_tune_evaluate`, `fine_tune_deploy` are scaffolded but the data
  pipeline (collector, anonymizer, quality filter) isn't built yet. Don't
  test FT — it's deliberately out of MVP scope.
- **GHL inbound webhooks**: the route accepts and logs events, but doesn't
  yet fan out to specific handlers (e.g. opportunity status updates → analytics
  events). Out of band for V1.
- **Email magic link redirect**: Supabase invite emails come from Supabase
  itself — make sure `Site URL` and `Redirect URLs` in Supabase Auth →
  Configuration include `web-merchant`'s URL.

---

## 11. Quick smoke (5 minutes, no external setup)

If you just want to verify the platform is alive without configuring
360dialog/GHL:

1. `curl https://api-production-6ac7.up.railway.app/health` → 200.
2. Sign in to web-admin → /dashboard renders with no console errors.
3. /merchants → "+ Nuovo merchant" → create one.
4. Click into it → "+ Invita utente" → invite yourself with another email.
5. Accept the invite → land on web-merchant.
6. /bot/playground → send "ciao" → bot replies in Italian within 5s.
7. /bot/config → fill business profile → playground reply changes to reflect it.

That exercises ES256 JWT verification, the bootstrap path, the cross-tenant
admin scope, the RLS-scoped merchant scope, the orchestrator, the model
router, the system prompt builder, and Supabase Realtime — without touching
WhatsApp or GHL at all.
