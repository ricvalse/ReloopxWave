# ADR 0012 — Merge the linear lifecycle flows into the automation graph

Status: Accepted — 2026-06-19
Extends [ADR 0011](0011-automation-flow-builder-and-template-validation.md) (which introduced the graph automations alongside the legacy linear `flows`).

## Context

ADR 0011 left two parallel systems: the legacy linear lifecycle `flows`/`flow_steps`
(4 fixed keys, scheduler-driven, edited in a list at `/flussi`) and the new graph
`automation_flows` (event-driven, edited on the canvas at `/automazioni`). The
product ask: **one "Automazioni" view listing every flow, with the canvas as the
single editor** — and the lifecycle flows must be editable on the canvas and gain
branching.

The hard constraint: the schedulers (`no_answer`, `reactivation`,
`appointment_reminder`) consume a flow step via `FlowRepository.resolve_step` →
`decide_outbound()` (the 24h-window compliance gate). That behavior must not change.

## Decision

The lifecycle flows become **system automations** on the graph model — one model,
one editor — while the compliance gate stays byte-for-byte identical.

- **`automation_flows.system_key`** (nullable, partial-unique per merchant) tags the
  4 lifecycle flows. NULL = custom (event-driven). System flows: trigger locked,
  non-deletable, excluded from the event dispatcher.
- **Unified `send` action node** carries the full `ResolvedFlowStep` surface
  (`window_policy`, `free_text`, `template_id`, `variable_mapping`); per-step
  `delay_minutes` becomes a `wait` node. Custom flows may also use `send` (the
  engine's `_do_action` honours the same window rules via `decide_outbound`).
- **The seam:** the schedulers swap `FlowRepository.resolve_step(key, step_index)`
  for `resolve_lifecycle_step(session, system_key, attempt_index, context)`, which
  **walks the system automation graph** (`ai_core.automations.resolve_send_node_at`
  — evaluates conditions, skips `wait`, counts `send` nodes) and returns the SAME
  `ResolvedFlowStep`. `decide_outbound` and `ResolvedFlowStep` are untouched — that
  immutability *is* the proof the gate is preserved. This is also what gives
  lifecycle flows branching (conditions evaluated at scheduler run-time).
- **No double execution:** `AutomationRepository.list_enabled_by_trigger` (the only
  dispatcher entry point) excludes `system_key IS NOT NULL`, plus a guard in
  `automation_run`. Schedulers remain the sole driver of lifecycle flows.
- **Disabled ≠ missing:** a disabled system flow returns `ResolvedFlowStep(flow_enabled=False)`
  (→ `decide_outbound` skips), NOT `None` (which would fall back to free text). This
  mirrors the legacy `resolve_step` semantics and is the highest-risk edge.
- **Data migration 0028** converts existing `flows`/`flow_steps` into system
  automation graphs (locked trigger → linear `wait`/`send` chain). `flows`/`flow_steps`
  are kept **deprecated** (dropped in a follow-up after FE cutover). The 4 system
  flows are otherwise **lazily seeded** on first API read.
- **UI:** a single `/automazioni` nav entry; the list groups Sistema vs Personalizzate;
  the canvas locks the trigger and offers only `condition`/`send`/`wait` for system
  flows. `/flussi` and its list editor are removed.

## Consequences

- **+** One model, one editor; lifecycle flows gain conditions/branches; no scheduler
  control-flow or compliance change.
- **+** Layering respected: the graph walk lives in `ai_core`; the compose step
  (`resolve_lifecycle_step`) lives in the worker layer (db must not import ai_core).
- **−** Branching for `no_answer`/`booking_reminder` is reliable only for
  `within_24h_window`/`time_of_day` today — their scheduler candidates don't carry
  score/last-message, so score/temperature/keyword conditions fail closed.
  `reactivation` carries `score` (enriched). Enriching the others is a follow-up.
- **−** `wait`-node delays on system flows are cosmetic in V1 (the scheduler owns
  timing); the resolver skips them.
- **−** Migration must drop `FORCE ROW LEVEL SECURITY` on `flows`/`flow_steps` *and*
  `automation_*` around the JWT-less conversion (a forced-RLS read returns 0 rows).
- Follow-ups: migration **0029** to drop `flows`/`flow_steps` + remove the `/flows`
  router/`FlowRepository`; `variable_mapping` UI for templated sends; two-tenant
  isolation test for `system_key`.
