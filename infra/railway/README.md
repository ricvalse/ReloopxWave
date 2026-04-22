# Railway deploy guide

End-to-end steps to deploy the Reloop AI backend (FastAPI + ARQ worker) to Railway. Frontend stays on Vercel — see `frontend/README.md`.

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

Then in the Railway dashboard, **add three resources** to the project:

| Service | Type | What it runs |
|---|---|---|
| `redis` | Database addon → Redis | ARQ queues + config cache + dedup keys |
| `api` | GitHub Repo (this repo) | FastAPI + alembic migrations |
| `worker` | GitHub Repo (this repo, same one) | Consolidated ARQ worker |

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
SUPABASE_DB_URL=postgres://postgres:<db-password>@db.<project-ref>.supabase.co:5432/postgres

# --- Redis (link from the Railway addon) ---
REDIS_URL=${{Redis.REDIS_URL}}

# --- AI providers ---
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=                       # optional
ANTHROPIC_FALLBACK_ENABLED=false

# --- Integrations (can be left blank initially; per-merchant overrides go via the admin UI) ---
GHL_CLIENT_ID=
GHL_CLIENT_SECRET=
WHATSAPP_APP_SECRET=
WHATSAPP_VERIFY_TOKEN=

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

- WhatsApp: `POST https://<api-domain>/webhooks/whatsapp/{phone_number_id}` (and the matching `GET` for Meta's verification challenge)
- GHL: `POST https://<api-domain>/webhooks/ghl/{merchant_id}`

## Rollbacks

Railway keeps every prior deploy. To roll back: open the api or worker service → **Deployments** → click the previous successful deploy → **Redeploy**. Migrations are forward-only by design (we don't auto-downgrade); a code rollback against a newer schema is safe as long as it doesn't read columns you've since dropped — and we haven't dropped anything yet.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Build fails on `uv sync --frozen` | `backend/uv.lock` out of sync with `pyproject.toml` | Run `cd backend && uv lock` locally and commit. |
| API returns 500, logs show `request.jwt.claims` errors | RLS policies couldn't read the claim — unusual; means the claim wasn't set | Verify the Supabase Auth hook injects `tenant_id`/`merchant_id`/`role` into JWTs. |
| Worker stuck on `relation "..." does not exist` | API migration didn't run | `railway run --service api alembic upgrade head`, then restart worker. |
| Webhook returns 401 | `WHATSAPP_APP_SECRET` mismatch | Re-paste from Meta App → Webhooks. |
| Healthcheck fails with timeout | App didn't bind to `$PORT` | Make sure the api service uses the Dockerfile, not a custom `startCommand` overriding the entrypoint. |
| `Invalid base64` on boot | `INTEGRATIONS_KEK_BASE64` malformed | Regenerate; must decode to exactly 32 bytes. |

## Staging vs production

Easiest path: a second Railway project (`reloop-ai-staging`) that points at the `develop` branch, with its own Supabase and Redis. Keeps blast radius small until you have proper preview environments wired up.
