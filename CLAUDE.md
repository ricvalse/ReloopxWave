# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This repo currently contains **only a design document** (`reloop-ai-architettura.md`, in Italian) for Reloop AI — there is no code, no build system, no tests, and no commits yet. The document is the authoritative source of truth for architecture, stack choices, data model, and the 13 use cases (UC-01 through UC-13). Read it before making any structural decisions; quote it with `reloop-ai-architettura.md:<line>` when confirming intent.

Before scaffolding anything, confirm with the user which area they want to start (frontend or backend) — they share one repo but have separate toolchains.

## Product shape (one paragraph)

Reloop AI is a **two-level multitenant SaaS**: agency tenants (admin panel) own many merchant sub-tenants (merchant portal). An AI agent runs conversations on WhatsApp Cloud API, reading/writing to the merchant's GoHighLevel (GHL) CRM. The differentiator is per-tenant fine-tuning of `gpt-4.1-mini` on real conversation logs (weeks 9–10 of the roadmap). Isolation between tenants is enforced by Postgres Row-Level Security keyed on Supabase JWT custom claims `tenant_id` and `merchant_id`.

## Planned architecture (the parts that require reading multiple sections)

**Single monorepo, two toolchains side-by-side** (section 3). Rationale: one developer on the project — operational simplicity wins over toolchain isolation. Do not try to split into separate repos, and do not introduce a cross-language build orchestrator (Nx, Bazel).

- `frontend/` — Turborepo + pnpm workspaces. Two Next.js 15 apps (`apps/web-admin`, `apps/web-merchant`) sharing `packages/ui`, `packages/api-client` (generated from backend OpenAPI), `packages/supabase-client`, `packages/config`.
- `backend/` — uv workspace. `services/api` (FastAPI), `workers/{conversation,scheduler,fine_tuning}` (ARQ on Redis), `libs/{ai_core,integrations,db,config_resolver,shared}`.
- `infra/{railway,docker}`, `docs/{decisions,runbooks}`, `scripts/`, `.github/workflows/` live at the repo root.

`cd frontend` or `cd backend` to enter the right world — each has its own package manager, lockfile, and lint/test commands. GitHub Actions uses **path filters** so changes in `frontend/**` only run frontend jobs and vice versa. A cross-cutting change (new FastAPI endpoint + UI that consumes it) ships as a single PR.

**Two data-access paths from the frontend** (section 4.4). The choice is deliberate — do not route everything through the backend:
- Direct to Supabase (`@supabase/supabase-js`) for auth, RLS-protected list/detail reads, Storage uploads, Realtime subscriptions.
- Through FastAPI (typed OpenAPI client) only when there is business logic, orchestration, or external side effects (onboarding, GHL OAuth, playground LLM calls, report generation).

Both paths present the same Supabase JWT; the backend verifies it via Supabase JWKS. RLS policies read the claims directly, so isolation holds regardless of which path is used.

**Worker consolidation (section 5.5, 13.2).** The three `workers/` directories are logical organization only — in production they deploy as a **single ARQ process** subscribed to all queues (`wa:inbound`, `scheduler:jobs`, `ft:pipeline`). Keep the code split by domain but register handlers in one `WorkerSettings`.

**Config resolution is a three-level cascade** (section 9): merchant override → agency default → system default. All lookups go through `libs/config_resolver.resolve()` with Redis caching (~60s TTL) and invalidation on write. For V1, overrides live in JSONB columns (`bot_configs.overrides`, `bot_templates.defaults`); the parameter schema is defined once in `libs/config_resolver/schema.py` and exported to the frontend via OpenAPI so both sides validate against the same Pydantic model. A dedicated `config_values` table is the V2 path if audit/diffs are needed — don't introduce it prematurely.

**Model routing** (section 6.7). `gpt-5-mini` is the default, `gpt-5-nano` for sentiment, `gpt-5.2` only on escalation triggers (long context, hot lead, critical objection keywords, long turn count). After fine-tuning ships, the per-tenant FT model on `gpt-4.1-mini` replaces the default for that tenant — rollout via A/B (UC-09), not a flag flip. Anthropic `claude-sonnet-4-6` is fallback-only, behind a feature flag.

**Vector search uses `pgvector` inside Supabase Postgres** (section 6.3, 8.2) — not Qdrant/Pinecone. This is an explicit choice so KB chunks inherit the same RLS, backup, and compliance as the rest of the data. Don't add an external vector DB.

**Use-case → component map is section 10.** When the user references "UC-XX", look there first to see which directories and libs are implicated.

## Stack (non-obvious choices)

- Backend package manager is **uv** (not Poetry); linter/formatter is **ruff**, type-checker **mypy**, testing **pytest + pytest-asyncio**, logging **structlog**.
- Frontend uses **TanStack Query** for server state and **Zustand** for local — do not add Redux. Types from the backend live in `frontend/packages/api-client/src/generated.ts` and are regenerated by `scripts/generate-api-types.sh` (spin up backend → download `openapi.json` → run `openapi-typescript`). Frontend CI runs this script and fails on uncommitted drift, so you must regenerate and commit after any FastAPI signature change (section 15: "Drift contratto OpenAPI").
- `@supabase/supabase-js` lives in `packages/supabase-client` specifically so the Supabase Auth dependency is swappable later (section 15: "Lock-in Supabase Auth"). Don't scatter direct Supabase imports through app code.
- Infra split: **Vercel** for frontend, **Railway** for FastAPI + ARQ worker + Redis, **Supabase Cloud (EU / Frankfurt)** for Postgres/Auth/Storage/Realtime. EU region is a GDPR requirement, not a preference.

## Security invariants (don't regress these)

- Every new table needs RLS policies keyed on `auth.jwt() ->> 'tenant_id'` and `merchant_id`. CI should include isolation tests with two tenants asserting cross-tenant reads fail (section 15).
- The backend may only use the Supabase **service role** for explicit admin operations (merchant creation, etc.), and every such call must be logged with `actor_id`. Don't reach for it as a convenience.
- External credentials (GHL, WhatsApp) are encrypted at rest with AES-256-GCM in the `integrations` table; the KEK is an env var.
- WhatsApp webhooks must validate HMAC-SHA256 signatures before enqueue; GHL webhooks use OAuth2 + signature. Drop unsigned events.
- Fine-tuning datasets must pass the `data_anonymizer` step (presidio + regex) before reaching OpenAI. This is contractual (Art. 5.2), not optional.

## Where decisions live

- Architecture-level choices that span frontend and backend belong as ADRs in `docs/decisions/` — this is the shared space explicitly called out in section 3. If you make a non-obvious call during implementation (e.g. picking JSONB over a config table, pinning a `pgvector` version), write an ADR there rather than burying it in a commit message.
- Operational procedures (restore drills, migrations, rollbacks) go in `docs/runbooks/`.

## Language and writing conventions

The architecture doc is in **Italian**. When writing code comments, commit messages, and PR descriptions, follow whatever the user uses in conversation — don't auto-translate the doc's terminology (e.g. "Dashboard Unificata", "Obiezioni") when referencing it, keep the Italian names so they match the spec.
