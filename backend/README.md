# Reloop AI — Backend

FastAPI + ARQ workers + AI core. Python 3.12, `uv` workspace.

## Layout

```
backend/
├── services/api/          # FastAPI app (public REST + webhooks)
├── workers/               # ARQ handlers, organised by domain, deployed as one process
│   ├── conversation/      # WhatsApp inbound (UC-01..05)
│   ├── scheduler/         # Cron-style jobs (UC-03, UC-06, UC-13, KPI rollup, KB reindex)
│   └── fine_tuning/       # FT pipeline (weeks 9-10)
├── libs/
│   ├── ai_core/           # Orchestrator, ModelRouter, RAG, scoring, classifiers
│   ├── integrations/      # GHL + WhatsApp Cloud API clients
│   ├── db/                # SQLAlchemy 2.0 models + Alembic migrations
│   ├── config_resolver/   # Three-level config cascade
│   └── shared/            # Settings, logging, errors
└── tests/                 # Cross-lib unit tests
```

## Develop

```bash
uv sync --all-packages                 # first time — installs every workspace member
uv run uvicorn api.main:app --reload   # API on :8000, hot reload
uv run arq workers.settings.WorkerSettings   # consolidated worker
```

> **Why `--all-packages`?** The workspace root (`backend/pyproject.toml`) has no
> runtime dependencies — it's just a workspace definition. Plain `uv sync` would
> install nothing. `--all-packages` tells uv to install every member (api,
> ai_core, integrations, db, config_resolver, shared) and their transitive deps,
> so console scripts like `alembic`, `arq`, `uvicorn` land in `.venv/bin`.

Common checks:

```bash
uv run ruff check .
uv run ruff format .
uv run mypy .
uv run pytest
uv run pytest tests/unit/test_scoring.py::test_empty_signals_zero_score
```

## Migrations

Requires Supabase CLI locally (`supabase start` spins Postgres at :54322).

```bash
uv run alembic upgrade head                         # apply
uv run alembic revision --autogenerate -m "add x"   # new migration (review carefully)
uv run alembic downgrade -1                         # rollback
```

## Worker consolidation

All three worker directories are registered as handlers in `workers/settings.py`.
In local dev and production, you run a single ARQ process that consumes every
queue. The split exists for code organisation only (section 5.5 of the spec).

## Service role vs tenant session

- `db.session.tenant_session(ctx)` — scoped to a Supabase JWT, enforces RLS via
  `SET LOCAL "request.jwt.claims"`. Use this everywhere by default.
- `db.session.session_scope()` — unscoped session. Only for explicitly
  documented admin tasks (merchant creation, system migrations). Always log
  with an `actor_id`.
