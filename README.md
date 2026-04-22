# Reloop AI

Multitenant SaaS with a WhatsApp AI agent for lead acquisition, integrated with GoHighLevel. See `reloop-ai-architettura.md` for the full architecture spec (Italian).

## Repo layout

```
reloop-ai/
├── frontend/       # Turborepo + pnpm — Next.js admin + merchant apps, shared packages
├── backend/        # uv workspace — FastAPI api, ARQ workers, AI core, integrations, db
├── infra/          # Railway + Docker configs
├── docs/           # ADRs and runbooks
├── scripts/        # Dev scripts (OpenAPI typegen, setup)
└── .github/        # Path-filtered CI
```

## Prerequisites

- Node.js 20+ and pnpm 10+
- Python 3.12 (uv will fetch it if missing)
- [uv](https://github.com/astral-sh/uv) 0.9+
- [Supabase CLI](https://supabase.com/docs/guides/cli) (local dev)
- Docker (optional, only for parity with Railway images)

## First-time setup

```bash
# Frontend
cd frontend
pnpm install

# Backend
cd ../backend
uv sync

# Local env
cp .env.example .env   # fill in Supabase URL, anon + service role keys, etc.
```

Start a local Supabase instance:

```bash
supabase start
```

Apply migrations:

```bash
cd backend
uv run alembic upgrade head
```

## Day-to-day

### Frontend

```bash
cd frontend
pnpm dev                    # both apps in parallel
pnpm dev --filter web-admin # only the admin app
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

### Backend

```bash
cd backend
uv run uvicorn api.main:app --reload     # API on :8000
uv run arq workers.settings.WorkerSettings   # consolidated worker
uv run ruff check .
uv run ruff format .
uv run mypy .
uv run pytest
uv run pytest tests/unit/test_x.py::test_y # single test
```

### OpenAPI types

After changing a FastAPI endpoint signature:

```bash
./scripts/generate-api-types.sh
```

This regenerates `frontend/packages/api-client/src/generated.ts`. CI fails if the file is out of date.

## Deploy

- **Backend** → Railway. Step-by-step guide: [`infra/railway/README.md`](infra/railway/README.md). Two services (`api`, `worker`) both built from this repo via `infra/docker/{api,worker}.Dockerfile`. Migrations run on api boot.
- **Frontend** → Vercel, root `frontend/`, auto-deploy on merge to main.
- **Database** → Supabase Cloud, EU (Frankfurt).
