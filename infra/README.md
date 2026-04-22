# Infrastructure

## Railway — backend

One project, three resources:

| Service | Type | Build file |
|---|---|---|
| `api` | GitHub repo (this repo) | `infra/docker/api.Dockerfile` |
| `worker` | GitHub repo (this repo) | `infra/docker/worker.Dockerfile` |
| `redis` | Railway Redis addon | — |

**Step-by-step deploy guide: [`railway/README.md`](railway/README.md).**

The TOML files in `railway/` are documentation of what the UI should be set to — they are not auto-discovered by Railway when builds are Dockerfile-based.

## Vercel — frontend

One project pointing at `frontend/` as root. Turborepo handles the rest.

- Preview deploys per PR, production on push to `main`.
- Env vars: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_BASE_URL`, plus `SENTRY_DSN_FRONTEND`, `POSTHOG_KEY`.

## Supabase Cloud — data platform

EU region (Frankfurt) for GDPR. One project per environment (`staging`, `production`). Custom JWT claims (`tenant_id`, `merchant_id`, `role`) are injected by an Auth hook — see `docs/runbooks/supabase-auth-hook.md` when it lands.
