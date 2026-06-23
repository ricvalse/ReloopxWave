# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This is an **implemented two-toolchain monorepo**, not a design doc anymore. Frontend (`frontend/`, Turborepo + pnpm) and backend (`backend/`, uv workspace) are both built out and deployed (Vercel + Railway + Supabase Cloud EU). `reloop-ai-architettura.md` (Italian) remains the **spec / source of truth** for intent — quote it as `reloop-ai-architettura.md:<line>` when confirming design decisions, but the code is now ahead of it in places (see "Deviations from the spec" below).

`cd frontend` or `cd backend` to enter the right world — each has its own package manager, lockfile, and lint/test commands. A cross-cutting change (new FastAPI endpoint + UI that consumes it) ships as a single PR.

**Current coverage:** all 13 use cases are implemented end-to-end on branch `feat/complete-use-cases` (the prior gaps in UC-04/05/09/11/12/13 and the fine-tuning pipeline were closed there). `docs/completion-plan.md` has the per-task status table and the verification notes — read it before starting feature work, and update it as items land. The caveats in "Deviations from the spec" below are the remaining sharp edges.

## Product shape (one paragraph)

Reloop AI is a **two-level multitenant SaaS**: agency tenants (admin panel, `web-admin`) own many merchant sub-tenants (merchant portal, `web-merchant`). An AI agent runs WhatsApp conversations, reading/writing the merchant's GoHighLevel (GHL) CRM. The differentiator is per-tenant fine-tuning of `gpt-4.1-mini` on real conversation logs. Tenant isolation is enforced by Postgres Row-Level Security keyed on Supabase JWT custom claims `tenant_id` and `merchant_id`.

## Build, test, run

**Backend** (`cd backend`, package manager **uv**):
- Tests: `uv run pytest tests/unit` (74 unit tests, fast, no DB). `uv run pytest tests/integration` needs a live Postgres (RLS isolation tests).
- Lint/format: `uv run ruff check` / `uv run ruff format`. Type check: `uv run mypy`.
- API entry: `services/api/src/api/main.py:create_app` → `app`. Run local: `uvicorn api.main:app --reload`.
- Worker entry: **`workers.settings.WorkerSettings`** (single consolidated ARQ process). Run local: `arq workers.settings.WorkerSettings`.

**Frontend** (`cd frontend`, package manager **pnpm** + Turborepo):
- `pnpm dev` / `pnpm lint` / `pnpm build` (via turbo). Two apps: `apps/web-admin`, `apps/web-merchant`.
- After any FastAPI signature change, regenerate the typed client: `scripts/generate-api-types.sh` (spin up backend → download `openapi.json` → `openapi-typescript` → `frontend/packages/api-client/src/generated.ts`). CI fails on uncommitted drift — regenerate and commit.

## Where the code lives (actual layout)

**Backend** (`backend/`):
- `services/api/src/api/` — FastAPI. `routers/` (auth, tenants, merchants, users, bot_config, knowledge_base, conversations, analytics, playground, ab_test, reports, integrations, webhooks, internal), `dependencies/` (JWT verify, tenant context, RBAC, DB session with `SET LOCAL` for RLS), `schemas/`, `core/`, `main.py`.
- `workers/` — **one ARQ process**, not three. `settings.py:WorkerSettings` registers all handlers + `cron_jobs`; `runtime.py` builds the shared context (router, WhatsApp sender, etc.). Domain modules: `conversation/handlers.py`, `scheduler/{no_answer,reactivation,objections,kpi_rollup,kb_reindex,integration_health,analytics_export}.py`, `fine_tuning/{collect,export,handlers}.py`. Cron-scheduled today: `followup_no_answer` (every 15m), `reactivate_dormant_leads` (daily 09:00), `daily_kpi_rollup`, `integration_health`. Other jobs are registered but **only invoked on demand** (`objection_extraction`, `kb_reindex`, `fine_tune_*`).
- `libs/ai_core/src/ai_core/` — `orchestrator.py` (entry per turn, structured-JSON actions), `conversation_service.py` (the real conversation pipeline), `router.py` (ModelRouter), `llm.py` (LLMClient impls), `scoring.py`, `objections.py`, `playground.py`, `actions/{booking,pipeline,scoring}.py`, `rag/{indexer,chunker,retriever}.py`, `ft/anonymizer.py`.
- `libs/integrations/src/integrations/` — `ghl/` (client, oauth, signatures), `whatsapp/` (`d360_client.py`, `factory.py`, `webhook.py`), `router/` (BSP routing layer), `supabase_admin.py`, `supabase_storage.py`.
- `libs/db/src/db/` — `models/` (tenant, bot, conversation, lead, kb, ab, analytics, integration, ft), `repositories/`, `migrations/versions/` (Alembic, currently up to 0028).
- `libs/config_resolver/src/config_resolver/` — three-level cascade + `schema.py` (Pydantic, all spec-9.4 keys with defaults/ranges).
- `libs/shared/src/shared/` — settings, logging (structlog), crypto, errors.

**Frontend** (`frontend/`):
- `apps/web-admin/src/app/` — `(app)/dashboard` (UC-12), `templates` (UC-10), `merchants`, `settings`, `billing`, `auth`, `login`. **Canonical routes live under the `(app)/` route group**; some empty top-level dirs (e.g. `app/dashboard/`) are dead leftovers — don't add pages there.
- `apps/web-merchant/src/app/` — `(app)/dashboard` (UC-11), `bot/{config,knowledge-base,playground,ab-testing}`, `conversations`, `reports/objections` (UC-13), `integrations`, `settings`.
- `packages/` — `ui` (shadcn-based primitives/patterns/shell/charts), `api-client` (generated from OpenAPI — never hand-edit `generated.ts`), `supabase-client` (the only place that imports `@supabase/supabase-js` — keep it swappable), `config`, `conversations` (shared conversation/composer components).

The **UC → component map is `reloop-ai-architettura.md` section 10** (lines ~635-650). When the user references "UC-XX", look there first.

## Architecture rules (don't regress these)

- **Single repo, two toolchains side-by-side.** Don't split into separate repos; don't introduce a cross-language build orchestrator (Nx, Bazel). GitHub Actions uses path filters (`frontend/**` vs `backend/**`).
- **Two data-access paths from the frontend** (spec 4.4) — deliberate, don't route everything through the backend: direct to Supabase (`@supabase/supabase-js`) for auth, RLS-protected list/detail reads, Storage uploads, Realtime; through FastAPI (typed OpenAPI client) only for business logic / orchestration / external side effects. Both present the same Supabase JWT; the backend verifies it via JWKS.
- **Workers deploy as one ARQ process** (`workers.settings.WorkerSettings`) subscribed to all queues. Keep code split by domain, register handlers in the one `WorkerSettings`.
- **Config resolution is a three-level cascade** merchant → agency → system, all via `config_resolver.resolve()` with Redis caching (~60s) and invalidation on write. Overrides in JSONB (`bot_configs.overrides`, `bot_templates.defaults`); the schema is defined once in `libs/config_resolver/schema.py` and exported to the frontend via OpenAPI. Don't add a `config_values` table (V2 path) prematurely.
- **Model routing** (spec 6.7): `gpt-5-mini` default, `gpt-5-nano` for sentiment, `gpt-5.2` on escalation (long context >4000 tok, hot lead, critical-objection keywords, many turns), `claude-sonnet-4-6` fallback behind a feature flag. The escalation triggers are implemented in `router.py`. The per-tenant FT-model override hook (`FtModelProvider`) exists but **is not yet wired** — see completion plan 2.5.
- **Vector search is `pgvector` inside Supabase Postgres** (HNSW, `vector_cosine_ops`, `<=>` operator) — no external vector DB. RLS applies to KB chunks too.

## Deviations from the spec (know these before editing)

- **WhatsApp uses 360dialog, not Meta Cloud API direct.** `integrations/whatsapp/d360_client.py` + `factory.py` + a `integrations/router/` BSP layer, with 360dialog Coexistence (mirrors phone-app messages). The spec still says "Meta BSP diretto" — treat 360dialog as the production reality.
- **GHL is a marketplace agency-install app (ADR 0007), CRM/calendar only.** The agency (= tenant) connects once from web-admin (`POST /integrations/ghl/agency/oauth/start`, `user_type="Company"`); locations arrive as `INSTALL` webhooks at `POST /webhooks/ghl/marketplace` (public-key signed, Ed25519 preferred / RSA legacy; same endpoint also receives GHL data events) and are minted into `ghl_location_tokens` (per-`locationId`) + linked to existing merchants from the admin UI. There is **no** per-merchant self-service GHL flow. Tokens live in the dedicated `ghl_agency_installs` / `ghl_location_tokens` tables, **not** `integrations` (WhatsApp only). The messaging channel stays 360dialog; GHL is contacts/opportunities/calendar. `IntegrationRepository.resolve_ghl(merchant_id)` reads the linked location token.
- **Sentry + PostHog are both wired** in `shared.observability` (`init_sentry`/`init_posthog`, called from `main.py` and the worker startup). Log aggregation is structlog → Railway logs.
- The 13-UC completion work landed on branch `feat/complete-use-cases` (see `docs/completion-plan.md` for the per-task status table). Net effect on what used to be gaps: A/B variants now run distinct prompts via `PromptManager` + `prompt_templates`; `SentimentAnalyzer` populates `lead.sentiment`; scoring is always-on/cumulative; `analytics_events` is published to Realtime (migration 0013); objection extraction auto-fires via the `close_idle_conversations` cron; the fine-tuning pipeline is chained end-to-end (`fine_tune_run`) with presidio NER + a real evaluator + FT routing via `FtModelResolver`.
- **Still partial:** unit coverage for DB-bound UCs (04/06/08/10/11/12/13) leans on the CI integration tests; the admin **templates** editor (UC-10) is now a per-field form with locks (no longer a JSON textarea), as is the merchant `bot-config-panel.tsx` Inherited/Customized/Locked UI; the FT pipeline + presidio + live conversation flow are only partially verifiable locally without external services (OpenAI FT, spaCy model, Supabase/Redis/360dialog) — but the FT chain and presidio NER are **implemented and wired** (anonymizer is regex+presidio, `FtModelResolver` is injected at both call-sites). For the **current verified status read `docs/audit-completamento-2026-06-22.md`** (supersedes the stale `completion-plan.md`): 0 blocker, 19 major, all 13 UC end-to-end.
- **CI debt (verify before relying on green):** the 12/06 commit zeroed the CI debt, but later commits **re-introduced** it — per the 2026-06-22 audit, `ruff check`/`ruff format --check`/`mypy` are red again on a handful of **new** files (audit §A, gaps #40–#42) and the frontend `vitest run` fails with no test files (#39). These are small/quick fixes, tracked in the audit. Don't mass-reformat the legacy files as a side effect of an unrelated change.

## Security invariants (don't regress these)

- Every new table needs RLS policies keyed on `auth.jwt() ->> 'tenant_id'` and `merchant_id`. Isolation tests with two tenants live in `backend/tests/integration/test_isolation*.py` — keep them passing and extend them for new tables.
- The backend may use the Supabase **service role** only for explicit admin operations (merchant creation, FT runs, etc.), and every such call must be logged with `actor_id`. Don't reach for it as a convenience.
- External credentials (GHL, WhatsApp) are encrypted at rest with AES-256-GCM in `integrations`; the KEK is an env var (rotation runbook: `docs/runbooks/rotate-kek.md`).
- WhatsApp/360dialog webhooks validate HMAC-SHA256 before enqueue. **All GHL webhooks now arrive on the single `/webhooks/ghl/marketplace` endpoint** (INSTALL/UNINSTALL **and** data events) signed with a **global published public key** — there is **no** per-merchant `/webhooks/ghl/{merchant_id}` HMAC endpoint anymore (it was removed). The signature uses two schemes (`marketplace_signatures.verify_ghl_marketplace_webhook`): **Ed25519** header `x-ghl-signature` (current/preferred) and **RSA-SHA256** header `x-wh-signature` (legacy, **deprecated by GHL 2026-07-01**). Ed25519 is verified first with **no fallback** when present (downgrade protection). Both keys are global published constants shipped as settings defaults (`ghl_marketplace_public_key_ed25519` / `ghl_marketplace_public_key`), overridable via env on rotation. Drop unsigned events.
- Fine-tuning datasets must pass `data_anonymizer` before reaching OpenAI (contractual, Art. 5.2). **This is a two-layer pipeline — regex + presidio NER — and presidio is implemented and required in production** (`libs/ai_core/src/ai_core/ft/presidio.py`, `build_presidio_transform`, spaCy `it_core_news_lg` shipped in the worker Dockerfile). Regex-only degradation is allowed only in non-prod/dev.

## Where decisions and procedures live

- ADRs in `docs/decisions/` (0001 monorepo, 0002 pgvector, 0003 consolidated worker, 0004 all-Railway deploy, 0005 360dialog channel creation, 0006 whatsapp templates + flows, 0007 GHL marketplace agency-install, 0008 persona strutturata + consegna "umana", 0009 playground = preview fedele del flusso WhatsApp reale, 0010 playground dry-run: simulazione tool + stato lead evolutivo + consegna realistica). Make a non-obvious call during implementation → write an ADR, don't bury it in a commit.
- Operational procedures in `docs/runbooks/` (go-live, ECIRCUITBREAKER recovery, migration rollback, KEK rotation, Supabase restore drill, FT deploy, GHL re-auth).
- The current verified V1 status is `docs/audit-completamento-2026-06-22.md` (supersedes the older `docs/completion-plan.md`, whose "Stato attuale" rows are stale). Keep the audit current as items ship.

## Language and writing conventions

The architecture doc is in **Italian**. Match the user's language in conversation for comments, commit messages, and PR descriptions; keep the spec's Italian terminology (e.g. "Dashboard Unificata", "Obiezioni", UC names) so it lines up with `reloop-ai-architettura.md`.
