# ADR 0011 — Visual automation flow builder ("lavagnetta") + WhatsApp template validation flow

Status: Accepted — 2026-06-19
Supersedes the "Visual flow builder rejected for V1" note in [ADR 0006](0006-whatsapp-templates-and-flows.md).

## Context

Two merchant-facing needs landed together:

1. **Custom automations with arbitrary triggers** — a drag-and-drop canvas (the
   "lavagnetta" the user asked for), classic automation-builder style: a trigger
   fans into conditions and actions.
2. **A complete template validation flow** — catch the silly Meta rejections
   ("Invalid Format", missing examples, bad language code, button violations)
   *before* the template is submitted to 360dialog/Meta.

ADR 0006 had deferred the visual builder and shipped only the linear lifecycle
`flows`/`flow_steps` (one ordered list per fixed `key`). That model can't express
branches, multiple custom flows per merchant, or non-lifecycle triggers.

## Decision

### A. Automations are a new graph model, separate from legacy lifecycle flows

We did **not** overload `flows`/`flow_steps` (its `(merchant_id, key)` uniqueness
allows one flow per key, and steps are linear). Instead a dedicated graph:

- `automation_flows` — one row per custom automation. Carries a denormalised
  `trigger_type` (+ `trigger_config`) derived from the single trigger node, so the
  worker dispatcher finds subscribers with one indexed lookup.
- `automation_nodes` — `kind ∈ {trigger, condition, action}`, a `type` per kind, a
  JSONB `config`, a client `node_key`, and canvas `position_x/y`.
- `automation_edges` — directed wires; `branch ∈ {default, true, false}` for the two
  sides of a condition.

All three are merchant-scoped with the **same RLS pattern as migration 0014**
(EXISTS-join through `merchants.tenant_id`, `ENABLE`+`FORCE`, identical predicate in
`USING`/`WITH CHECK`, `merchant_id` denormalised onto nodes/edges). Migration **0026**.

Product naming: the new feature is **"Automazioni"**; the legacy lifecycle lists
stay **"Flussi"**. Two distinct surfaces, no migration of the old data.

Node taxonomy (V1):
- **Triggers**: `message_received`, `no_answer`, `booking_created`, `booking_failed`,
  `lead_dormant`.
- **Conditions**: `lead_temperature`, `lead_score`, `within_24h_window`,
  `time_of_day`, `message_contains`.
- **Actions**: `send_template`, `send_message`, `wait`.

The canvas is authoritative: every save replaces the whole node/edge set. A draft
(`enabled=false`) may be saved incomplete; **enabling** requires a valid graph (one
trigger, no incoming edge to it, acyclic, required action config). Validation,
condition evaluation, and traversal are pure functions in
`ai_core/automations.py`, shared by the API router and the worker engine and
unit-tested without a DB.

### B. The execution engine tails `analytics_events` (no hot-path coupling)

Rather than add emit hooks across `conversation_service` / the schedulers (and risk
the live conversation path), the worker `automation_dispatch` cron **tails
`analytics_events`** with a Redis cursor and, per matching event, enqueues an
`automation_run` for each enabled subscriber. `automation_run` walks the graph,
evaluates conditions, executes actions, and re-enqueues a deferred continuation for
`wait` nodes. Trigger mapping:

| analytics event | trigger |
|---|---|
| `message.received` | `message_received` |
| `booking.created` / `booking.failed` | `booking_created` / `booking_failed` |
| `reminder.sent` | `no_answer` |
| `lead_reactivation.sent` | `lead_dormant` |

All WhatsApp sends go through the same 24h-window rules as `workers.outbound`:
free-text `send_message` only inside the window; `send_template` (approved template)
anywhere. De-dup per `(automation, event)` in Redis.

### C. Template validation is backend-authoritative, frontend-mirrored

`integrations/whatsapp/templates.lint_template` is the single source of truth. It
now returns `LintIssue` with a `severity` (`error` blocks submit, `warning` is
advisory) and a `field`, and covers the full Meta ruleset: language format +
supported-code check, body whitespace (tabs / >4 spaces / >4 newlines = error),
per-`{{n}}` example presence (warning), full button caps (≤10 total, URL ≤2, phone
≤1, copy-code ≤1, https-only, label ≤25, E.164 phone), AUTHENTICATION guards, and a
promo-in-UTILITY reclassification warning. The frontend `whatsapp-template-lint.ts`
mirrors the deterministic rules for live UX only.

New endpoints: `POST /whatsapp-templates/validate` (server-authoritative pre-flight),
`PUT /whatsapp-templates/{id}` (edit a draft/rejected template → resets to draft),
`POST /whatsapp-templates/{id}/submit` (push a draft to Meta with a fresh name), and
`as_draft` on create. `body_examples` is now persisted (migration 0025) so a draft
keeps its examples through to submit. The pre-submit flow is:
`draft → validate → preview (WhatsApp bubble) → submit → poll status → surface rejection`.

## Consequences

- **+** The "lavagnetta" is real graph infra, not a linear list; conditions/branches
  are first-class; many automations per merchant.
- **+** Zero changes to the live conversation/booking paths — the engine only reads
  events those paths already emit. Safe to ship alongside in-flight work there.
- **+** Silly template rejections are caught locally, both client- and server-side.
- **−** The dispatcher polls (≤60s latency) and triggers off *outbound* signals for
  `no_answer`/`lead_dormant` (the follow-up/reactivation already fired). A future
  iteration can add dedicated "lead went silent" / "dormant N days" emits.
- **−** New dependency on the frontend: React Flow (`@xyflow/react`) for the canvas.
- Migrations **0025** (template examples) and **0026** (automation graph). New RLS
  tables need their two-tenant isolation test extended.
