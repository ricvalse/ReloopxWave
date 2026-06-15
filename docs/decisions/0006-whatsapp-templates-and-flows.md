# ADR 0006 — WhatsApp template engine + "Flussi" outbound sequences

Status: Accepted (2026-06-14)

## Context

Reloop's proactive outbound messages (UC-03 no-answer follow-ups, UC-06 dormant-lead
reactivation, UC-02 booking confirmation) all sent **free-form text** via the 360dialog
`/messages` endpoint. WhatsApp's policy only permits free-form messages within the **24h
customer-service window** (24h since the customer's last inbound message); outside it,
only a Meta-**approved template** may be sent. Reactivation fires at ≥90 days dormant and
the 2nd no-answer reminder at ~24h — both land *outside* the window, so those sends were
non-compliant and would be rejected by Meta in production. This was a correctness bug, not
just a missing feature.

The sibling project `/Users/riccardo/Progetti/Amalia/amalia-ai` already runs a full template
lifecycle against the same 360dialog Partner (submit → approval webhook/poll → send with
parameters), plus a campaigns layer. We ported the template *engine* shape from it (TS+Py →
our Python), but not its campaigns model.

A second question was whether to introduce a "Flussi" (flows) concept. Reloop already *has*
flows — they're hardcoded in the schedulers (no-answer = step@120m → step@24h; reactivation
= step@90d → step@7d). Adding templates without a container would scatter the template
bindings across config keys.

## Decision

Three layers (see `docs/plans/whatsapp-templates-flussi.md`):

1. **Template engine** — `whatsapp_templates` table (per-merchant, RLS); `D360TemplateClient`
   for create/status/delete against `/v1/configs/templates`; pure builder + linter
   (`integrations.whatsapp.templates`); approval-status sync via webhook
   (`message_template_status_update`, HMAC-validated, enqueued to ARQ) **and** an hourly
   `template_status_sync` cron fallback; CRUD API at `/whatsapp-templates`.

2. **24h-window dispatcher** — `workers.outbound.decide_outbound` is the single enforcement
   point: inside the window → free text; outside → approved template, else **skip** (never
   free-form). Drives a new `conversations.last_inbound_at` column (bumped only on inbound;
   `last_message_at` couldn't serve this because outbound sends bump it too). Wired into
   no-answer and reactivation.

3. **Flussi (scope A)** — `flows` / `flow_steps` tables holding configurable outbound
   sequences; the schedulers read a step via `FlowRepository.resolve_step` and fall back to
   their built-in copy when no flow is configured. Merchant UI at `/flussi` and
   `/whatsapp-templates`.

Sub-decisions:

- **Dedicated tables over JSONB overrides for flows.** The cascade-config JSONB bag
  (`bot_configs.overrides`) is great for scalar knobs, but flows are relational (a flow has
  N ordered steps, each FK-linked to a template). Modelling that in JSONB would mean
  hand-rolling ordering, referential integrity, and step-level RLS. Dedicated tables get all
  three for free and keep the scheduler queries simple.
- **`merchant_id` denormalised onto `flow_steps`.** So the standard `merchant_isolation_*`
  RLS predicate (EXISTS join through `merchants.tenant_id`) applies without a second join
  through `flows`.
- **No config-keys for template binding.** The flow steps own the binding; adding parallel
  `*_template_name` config keys would duplicate the mechanism and create drift.
- **No edit-lineage in V1.** Amalia's supersede/repoint machinery (a new row replaces an
  approved one, FKs auto-advance on approval) is complex; V1 editing recreates + re-selects.

## Rejected alternatives

- **Keep sending free text outside the window** — non-compliant; Meta rejects it. This is
  the bug we're fixing.
- **Campaigns/broadcast layer now (Amalia's `Campaign`/`CampaignRecipient`)** — high
  marketing value but a much larger epic (audience builder, recipient snapshot, throttling,
  click redirector). Deferred to V2.
- **Visual flow builder (drag-and-drop canvas)** — over-scoped for V1; the lifecycle flows
  are short ordered lists, so a list editor suffices.
- **Booking-reminder + first-contact trigger jobs** — the flow keys and dispatcher support
  both, but the trigger jobs are deferred: a reminder job needs an upcoming-appointments
  source (we have no local appointments table; bookings live in GHL), and first-contact
  needs a merchant-initiated outreach trigger that doesn't exist yet. Data model is ready.

## Consequences

Positive:
- Proactive sends are now WhatsApp-compliant: never free-form outside the 24h window.
- Skips are observable (`reminder.skipped` / `lead_reactivation.skipped` analytics with a
  reason) instead of silent provider rejections.
- Merchants self-serve templates and tune lifecycle sequences without code.
- The engine generalises: new lifecycle flows need a key + steps, not a new scheduler.

Negative / watch:
- A merchant with no approved template for reactivation/no-answer #2 now **skips** those
  sends rather than sending free text. This is correct, but it means those automations are
  effectively off until a template is approved — surfaced in the Flussi UI with a warning.
- Approval latency is external (Meta). The webhook is primary; the hourly cron is the
  backstop. A template stuck `pending_approval` simply doesn't fire its step.
- `last_inbound_at` is NULL on conversations created before migration 0014 → treated as
  "outside window", which is the safe default (prefers template/skip over a risky free-form).
