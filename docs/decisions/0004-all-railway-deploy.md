# ADR 0004 — All-Railway deploy (frontend included)

Status: Accepted (2026-04-23)

Supersedes the "Vercel for frontend, Railway for backend" split mentioned in `reloop-ai-architettura.md` §Infrastruttura.

## Context

The architecture document assumed Vercel for the two Next.js apps (`web-admin`, `web-merchant`) and Railway for the FastAPI API + ARQ worker + Redis. This split is the industry default but it introduces two billing relationships, two deploy platforms, and two different log aggregators to monitor at 3 a.m.

The project has one developer. The traffic volume does not justify Vercel's edge-optimised pricing. Both Next.js apps run as standard Node servers and do not use Vercel-specific primitives (edge middleware, ISR on-demand revalidation, Vercel Analytics).

## Decision

Deploy **both frontends and the backend on Railway**, in the same project. Each Next.js app ships as a dockerised `next start` service built from `infra/docker/web.Dockerfile`. The API + worker already live there.

The `vercel:*` commands in the Claude Code plugin set and the `vercel.json` in the repo remain dormant — they're not removed because migrating back to Vercel stays a one-command path if the trade-offs flip.

## Consequences

Positive:
- Single Railway project to monitor; one `railway logs` command surfaces errors for any surface.
- Shared `pnpm install` cache across the monorepo builds (Turborepo remote cache in CI).
- Consistent env-var management: no split between Vercel project settings and Railway service env.
- The WhatsApp/GHL webhook targets, the frontend URLs, and the API all resolve to the same Railway DNS suffix, simplifying CORS + redirect_uri allowlists.

Negative / watch:
- No edge network for the frontends — cold starts are longer outside the `eu-west` region. Mitigation: EU-concentrated user base (§1 GDPR requirement) means the Frankfurt-adjacent Railway region is fine.
- No built-in preview environments per-PR. Tracked as a follow-up in `docs/runbooks/`. For now, preview happens against local + staging.
- If we later need ISR / on-demand revalidation, Next.js on a Node server still supports it but with self-managed cache — so this is a "revisit if" trigger.

## Revisit if

- We need edge middleware / geographic routing — Vercel's edge runtime is purpose-built and reimplementing it on Railway is not worth it.
- A staging pipeline requires per-PR preview URLs with automatic Supabase branch linking — Vercel's preview-branch story is materially better than Railway's.
- Production frontend latency from outside the EU region becomes a user complaint.

## Where the work landed

- `infra/railway/README.md` — service-by-service deploy notes.
- `infra/docker/web.Dockerfile` — shared Dockerfile for both Next.js apps.
- `docs/runbooks/ecircuitbreaker-recovery.md` — Supavisor-specific ops note that exists only because the DB connection is Supabase-routed regardless of frontend platform.
