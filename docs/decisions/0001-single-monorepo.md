# ADR 0001 — Single monorepo for frontend and backend

Status: Accepted (2026-04-21)
Supersedes: an earlier draft that proposed two parallel repos.

## Context

The product is a two-level multitenant SaaS (section 1 of `reloop-ai-architettura.md`). The frontend is TypeScript (Next.js 15 + Turborepo), the backend is Python 3.12 (FastAPI + ARQ workers). They are connected by an auto-generated OpenAPI contract. One developer is shipping V1.

## Decision

Put both stacks in **one Git repo**, side-by-side under `frontend/` and `backend/`. Keep their native toolchains unchanged — pnpm + Turborepo inside `frontend/`, uv inside `backend/`. Do not introduce a cross-language build orchestrator (Nx, Bazel). GitHub Actions uses path filters to scope jobs.

## Consequences

Positive:
- One `git clone`, one issue tracker, one PR for cross-cutting changes (new FastAPI endpoint + UI that consumes it).
- ADRs and runbooks that span both worlds live in `docs/` with no cross-repo linking.
- Deploys are still independent: Vercel points at `frontend/`, Railway points at `backend/`. The monorepo is logical, not build-system level.

Negative / watch:
- Path filters must stay correct or CI will over-trigger. If that becomes brittle, revisit.
- Lockfile ownership is split (`frontend/pnpm-lock.yaml`, `backend/uv.lock`). Acceptable — each tool is authoritative for its half.

## Alternatives considered

- **Two parallel repos** (initial draft). Rejected: one-developer overhead of syncing breaking changes across two PRs outweighed the hypothetical "clean separation" benefit.
- **Nx or Bazel for cross-language builds**. Rejected: solves a problem we don't have and adds a third toolchain.

## Revisit if

- Team grows past ~4 engineers and ownership splits cleanly along the frontend/backend line.
- CI times on cross-cutting PRs regularly exceed 10 minutes because path filters force full re-runs.
