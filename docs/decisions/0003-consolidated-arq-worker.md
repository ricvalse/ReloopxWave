# ADR 0003 — Three worker domains, one ARQ process

Status: Accepted (2026-04-21)

## Context

The backend has three worker domains (section 5 of the architecture doc): `conversation` (WhatsApp inbound), `scheduler` (follow-ups, reactivation, KPI rollups, objection extraction, KB reindex), and `fine_tuning` (weeks 9-10 pipeline).

## Decision

Keep the code organised in three directories under `backend/workers/`, but register every handler under a **single `WorkerSettings`** in `backend/workers/settings.py`. Railway deploys it as one service consuming every ARQ queue.

## Consequences

Positive:
- One idle process instead of three. Cheaper on Railway, simpler to deploy, one set of logs/alerts.
- Handlers can still be independently enabled/disabled via the `functions` list.

Negative / watch:
- Single point of failure for all async processing. Mitigation: Sentry on the process, Railway auto-restart on crash, queue-backlog alerts. If backlog becomes a regular problem, split `ft:pipeline` (longest-running jobs) into its own service — the code structure already supports it.

## Revisit if

- A long-running FT job starves the `wa:inbound` queue for more than a minute under steady load.
- CPU usage on the worker instance saturates during normal operation.
