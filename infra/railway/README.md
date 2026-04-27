# Railway deploy guide

End-to-end steps to deploy all of Reloop AI (FastAPI + ARQ worker + both Next.js apps) to Railway. This replaces the original split of frontend-on-Vercel / backend-on-Railway — keeping everything in one provider trades Vercel's Next.js-specific perks (edge CDN, preview URLs, `next/image` optimization) for one bill, one login, and private networking between frontend and API.

> Architecture note: the system design doc (`reloop-ai-architettura.md`, section 13.1) still references Vercel. That split remains a valid target — the Dockerfiles here are a pragmatic first-ship, not a permanent rejection of the original plan.

## Prerequisites

- Railway account + CLI: `brew install railway` (or follow [docs.railway.com](https://docs.railway.com/guides/cli))
- Supabase project (EU region per spec section 13.1)
- OpenAI API key, optionally Anthropic
- WhatsApp Business + GoHighLevel credentials *(can be added after the first deploy)*

## One-time setup

### 1. Generate the integrations KEK

The `integrations` table stores per-merchant secrets encrypted with AES-256-GCM. The KEK lives in env, never in the repo:

```bash
cd backend
uv sync --all-packages
uv run python -c "from shared.crypto import generate_kek_base64; print(generate_kek_base64())"
```

Save that base64 string — you'll paste it as `INTEGRATIONS_KEK_BASE64` in Railway. **If you lose it, every encrypted integration secret is unrecoverable.** Back it up to your password manager.

### 2. Create the Railway project

```bash
railway login
railway init    # creates a new project; pick a name like "reloop-ai-prod"
```

Then in the Railway dashboard, **add five resources** to the project:

| Service | Type | What it runs |
|---|---|---|
| `redis` | Database addon → Redis | ARQ queues + config cache + dedup keys |
| `api` | GitHub Repo (this repo) | FastAPI + alembic migrations |
| `worker` | GitHub Repo (this repo, same one) | Consolidated ARQ worker |
| `web-admin` | GitHub Repo (this repo, same one) | Next.js agency control panel |
| `web-merchant` | GitHub Repo (this repo, same one) | Next.js merchant portal |

### 3. Configure the `api` service

In the service settings:

- **Source**: this GitHub repo, branch `main`
- **Build**: Dockerfile, path `infra/docker/api.Dockerfile`, build context = repo root (default)
- **Watch Paths** *(optional but recommended)*: `backend/**`, `infra/docker/**` — stops frontend-only commits from rebuilding the backend
- **Networking**: enable a public domain (or attach a custom one). Railway will inject `$PORT`; the entrypoint binds to it.
- **Healthcheck**: path `/health`, timeout 30s
- **Replicas**: 1 for now

### 4. Configure the `worker` service

- **Source**: same repo + branch as `api`
- **Build**: Dockerfile, path `infra/docker/worker.Dockerfile`
- **Watch Paths** *(optional)*: `backend/**`, `infra/docker/**`
- **Networking**: NO public domain (workers don't serve HTTP)
- **Healthcheck**: leave blank
- **Replicas**: 1, **disable scale-to-zero** (cold start would hurt first-message latency — section 15 of the architecture doc)

### 4a. Configure the `web-admin` and `web-merchant` services

Both Next.js apps share one `infra/docker/web.Dockerfile`, parameterised by `APP_NAME`. Per-service settings:

- **Source**: same repo + branch as `api`
- **Build**: Dockerfile, path `infra/docker/web.Dockerfile`
- **Watch Paths** *(optional)*: `frontend/**`, `infra/docker/web.Dockerfile`
- **Networking**: enable a public domain (Railway injects `$PORT`; the Next standalone server reads it)
- **Healthcheck**: path `/` (Next returns 200 at root once the server is up)
- **Replicas**: 1

Service variables (per service — the two `APP_NAME` values differ):

```text
APP_NAME=web-admin                       # or web-merchant on the other service
RAILWAY_DOCKERFILE_PATH=infra/docker/web.Dockerfile

# NEXT_PUBLIC_* are inlined into the JS bundle at build time. They must also
# be declared as ARG in the Dockerfile (they already are) — Railway forwards
# matching service vars as build args.
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key>
NEXT_PUBLIC_API_BASE_URL=https://<your-api-domain>.up.railway.app
NEXT_PUBLIC_SENTRY_DSN=                  # optional
NEXT_PUBLIC_POSTHOG_KEY=                 # optional
```

The web services don't need Supabase service-role, DB URL, or the KEK — those are backend-only.

### 5. Wire shared environment variables

Both services need the same env. Easiest path: create a Railway **shared variable group** and attach to both. Required keys:

```text
ENVIRONMENT=production
LOG_LEVEL=info

# --- Supabase (paste the raw connection string; the api auto-rewrites the scheme) ---
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<from Project Settings → API>
SUPABASE_SERVICE_ROLE_KEY=<from Project Settings → API>
SUPABASE_JWT_SECRET=<from Project Settings → API → JWT Settings>
# Use the Supavisor POOLER URL (session mode, port 5432), not the direct
# db.<ref>.supabase.co endpoint. The direct endpoint is IPv6-only and Railway
# egress can't reach it reliably. Pooler has IPv4 and works everywhere.
#
# Find it in Supabase dashboard → Project Settings → Database → Connection
# pooling → "Session" mode → URI. The format is:
#   postgresql://postgres.<project-ref>:<db-password>@aws-0-<region>.pooler.supabase.com:5432/postgres
#
# Use port 5432 (session mode), NOT 6543 (transaction mode) — asyncpg +
# Alembic rely on prepared statements that transaction mode breaks.
SUPABASE_DB_URL=postgresql://postgres.<project-ref>:<db-password>@aws-0-<region>.pooler.supabase.com:5432/postgres

# --- Redis (link from the Railway addon) ---
REDIS_URL=${{Redis.REDIS_URL}}

# --- AI providers ---
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=                       # optional
ANTHROPIC_FALLBACK_ENABLED=false

# --- Integrations (can be left blank initially; per-merchant overrides go via the admin UI) ---
GHL_CLIENT_ID=
GHL_CLIENT_SECRET=
WHATSAPP_PARTNER_API_KEY=
WHATSAPP_PARTNER_ID=

# --- Crypto ---
INTEGRATIONS_KEK_BASE64=<from step 1>

# --- Observability ---
SENTRY_DSN_BACKEND=                      # optional but recommended
POSTHOG_KEY=                             # optional
```

Optional knobs:

```text
WEB_CONCURRENCY=2     # uvicorn worker count (api only)
RUN_MIGRATIONS=1      # set to 0 to skip alembic on container boot (api only)
```

### 6. First deploy

Push to `main` (or trigger a manual deploy). Railway builds both Dockerfiles in parallel.

The first time the `api` container boots, the entrypoint runs `alembic upgrade head` against your Supabase Postgres — that's where the schema, RLS policies, and `pgvector` extension get created. Watch the api logs until you see `▶ uvicorn on :<PORT>`.

Verify:

```bash
curl https://<your-api-domain>.up.railway.app/health
# {"status":"ok","environment":"production"}
```

The worker may log `relation does not exist` on its first poll if it boots before the api finishes migrating. ARQ retries with backoff, so it self-heals within seconds — you don't need to do anything.

## Day-to-day

```bash
# tail logs
railway logs --service api
railway logs --service worker

# run a one-off command in the api environment (e.g. manual migration)
railway run --service api alembic upgrade head
railway run --service api python -c "from shared.crypto import generate_kek_base64; print(generate_kek_base64())"

# environment variable round-tripping
railway variables --service api
railway variables set SOME_KEY=value --service api

# trigger a redeploy without pushing
railway up --service api
```

## Webhook URLs to register

Once `api` is live, register these with the providers (per-merchant — typically via the in-app integrations flow rather than directly in Meta/GHL):

- WhatsApp: `POST https://<api-domain>/webhooks/whatsapp/{phone_number_id}` (configured per-channel in the 360dialog Partner Hub; no signature secret to set)
- GHL: `POST https://<api-domain>/webhooks/ghl/{merchant_id}`

## Rollbacks

Railway keeps every prior deploy. To roll back: open the api or worker service → **Deployments** → click the previous successful deploy → **Redeploy**. Migrations are forward-only by design (we don't auto-downgrade); a code rollback against a newer schema is safe as long as it doesn't read columns you've since dropped — and we haven't dropped anything yet.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Build fails on `uv sync --frozen` | `backend/uv.lock` out of sync with `pyproject.toml` | Run `cd backend && uv lock` locally and commit. |
| API returns 500, logs show `request.jwt.claims` errors | RLS policies couldn't read the claim — unusual; means the claim wasn't set | Verify the Supabase Auth hook injects `tenant_id`/`merchant_id`/`role` into JWTs. |
| Worker stuck on `relation "..." does not exist` | API migration didn't run | `railway run --service api alembic upgrade head`, then restart worker. |
| GHL webhook returns 401 | `GHL_WEBHOOK_SECRET` mismatch | Re-paste from your GHL marketplace app settings. |
| Healthcheck fails with timeout | App didn't bind to `$PORT` | Make sure the api service uses the Dockerfile, not a custom `startCommand` overriding the entrypoint. |
| `Invalid base64` on boot | `INTEGRATIONS_KEK_BASE64` malformed | Regenerate; must decode to exactly 32 bytes. |
| `OSError: [Errno 101] Network is unreachable` to Supabase | `SUPABASE_DB_URL` points at the direct (IPv6-only) endpoint | Switch to the Supavisor pooler URL (`postgres.<ref>@aws-0-<region>.pooler.supabase.com:5432`). |
| Redis connection refused at `localhost:6379` | `REDIS_URL` not set on the service | Set `REDIS_URL=${{Redis.REDIS_URL}}` on **both** api and worker. |

## Staging vs production

Easiest path: a second Railway project (`reloop-ai-staging`) that points at the `develop` branch, with its own Supabase and Redis. Keeps blast radius small until you have proper preview environments wired up.
