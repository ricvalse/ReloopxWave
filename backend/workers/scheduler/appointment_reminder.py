"""UC-02 — appointment reminders.

Sends a WhatsApp reminder ahead of a booked appointment. The local
`appointments` mirror (write-through at booking + reconcile poll) is the source
of truth for upcoming slots, so this job just scans it for appointments whose
`reminder_due_at <= now` (multi-reminder support: ogni merchant può configurare
più orari di anticipo tramite ConfigKey.BOOKING_REMINDER_SCHEDULE).

A reminder for an appointment booked days ago is almost always OUTSIDE the 24h
window, so — like reactivation — it goes through `decide_outbound` and is sent
only when an approved `booking_reminder` template (or the within-window free
text) is available; otherwise it is skipped and retried on the next tick until
the slot passes. Idempotency is gestita da `reminder_schedule[].sent_at` nel DB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ai_core.automations import SendPlan, resolve_send_node_at, resolve_send_plan
from db import (
    AnalyticsRepository,
    AppointmentReminderCandidate,
    AppointmentRepository,
    AutomationRepository,
    ConversationRepository,
    IntegrationRepository,
    ResolvedFlowStep,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.models import WhatsAppTemplate
from integrations.whatsapp.factory import build_whatsapp_sender
from shared import get_logger
from workers.outbound import (
    MODE_SKIP,
    decide_outbound,
    is_within_24h,
    send_and_persist_decision,
)

logger = get_logger(__name__)


async def _enabled_booking_flow(session: Any, merchant_id: Any) -> Any | None:
    """The merchant's enabled `booking_created` automation (ADR 0015 — appointment
    reminders are sourced from a normal automation, not a system flow)."""
    autos = await AutomationRepository(session).list_enabled_by_trigger(
        merchant_id=merchant_id, trigger_type="booking_created"
    )
    return autos[0] if autos else None


def _graph(flow: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = [
        {"node_key": n.node_key, "kind": n.kind, "type": n.type, "config": n.config or {}}
        for n in flow.nodes
    ]
    edges = [
        {"source_key": e.source_key, "target_key": e.target_key, "branch": e.branch}
        for e in flow.edges
    ]
    return nodes, edges


async def _resolve_booking_plan(
    session: Any, *, merchant_id: Any, context: dict[str, Any]
) -> SendPlan | None:
    """Send plan of the enabled booking_created automation (None if absent)."""
    flow = await _enabled_booking_flow(session, merchant_id)
    if flow is None:
        return None
    nodes, edges = _graph(flow)
    return resolve_send_plan(nodes, edges, context=context)


async def _resolve_booking_step(
    session: Any, *, merchant_id: Any, attempt_index: int, context: dict[str, Any]
) -> ResolvedFlowStep | None:
    """ResolvedFlowStep for the attempt_index-th reminder send. None → no enabled
    flow (or fewer sends than attempt_index) → decide_outbound SKIPs (no_flow)."""
    flow = await _enabled_booking_flow(session, merchant_id)
    if flow is None:
        return None
    nodes, edges = _graph(flow)
    cfg = resolve_send_node_at(nodes, edges, attempt_index=attempt_index, context=context)
    if cfg is None:
        return None
    template: WhatsAppTemplate | None = None
    template_id = cfg.get("template_id")
    if template_id:
        try:
            template = await session.get(WhatsAppTemplate, UUID(str(template_id)))
        except (ValueError, TypeError):
            template = None
    return ResolvedFlowStep(
        flow_enabled=True,
        step_enabled=True,
        window_policy=str(cfg.get("window_policy", "auto")),
        free_text=cfg.get("free_text"),
        variable_mapping=dict(cfg.get("variable_mapping") or {}),
        template_name=template.name if template else None,
        template_language=template.language if template else None,
        template_variables=list(template.variables) if template else [],
        template_approved=bool(template and template.status == "approved"),
    )


async def send_appointment_reminders(ctx: dict[str, Any]) -> dict[str, Any]:
    settings = ctx["settings"]
    now = datetime.now(tz=UTC)
    candidates = await _scan(now=now)
    logger.info("uc02.reminder.scan", count=len(candidates))

    sent = 0
    for cand in candidates:
        if await _maybe_send(cand, now=now, kek=settings.integrations_kek_base64):
            sent += 1
    return {"candidates": len(candidates), "sent": sent}


async def _scan(*, now: datetime) -> list[AppointmentReminderCandidate]:
    async with session_scope() as session:
        return await AppointmentRepository(session).list_due_for_reminder(now=now)


def _format_slot(start_at: datetime, tz_name: str | None) -> str:
    try:
        tz = ZoneInfo(tz_name or "Europe/Rome")
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("Europe/Rome")
    local = start_at.astimezone(tz)
    return local.strftime("%d/%m alle %H:%M")


async def _maybe_send(cand: AppointmentReminderCandidate, *, now: datetime, kek: str) -> bool:
    tenant_ctx = TenantContext(
        tenant_id=cand.tenant_id,
        merchant_id=cand.merchant_id,
        role="worker",
        actor_id=cand.merchant_id,
    )

    async with tenant_session(tenant_ctx) as session:
        appts = AppointmentRepository(session)
        analytics = AnalyticsRepository(session)

        # No hardcoded copy: the reminder text comes solely from the send node's
        # `free_text` on the lavagnetta (via `step`), which can render
        # `{{appointment.datetime}}` from the context below. A blank send → skip.
        when = _format_slot(cand.start_at, cand.tz_name)

        within_window = is_within_24h(cand.last_inbound_at, now)
        plan_context = {
            "within_24h_window": within_window,
            "minutes_of_day": now.hour * 60 + now.minute,
        }
        # Multi-reminder: pick the `send` node whose «attendi fino a X ore prima»
        # matches the offset firing now, so each reminder carries its own copy.
        # Falls back to the first send (attempt 0) when there is no enabled graph
        # or no matching offset (e.g. a config-driven schedule).
        attempt_index = 0
        if cand.reminder_due_at is not None:
            hours_before = round((cand.start_at - cand.reminder_due_at).total_seconds() / 3600)
            plan = await _resolve_booking_plan(
                session,
                merchant_id=cand.merchant_id,
                context=plan_context,
            )
            if plan is not None:
                attempt_index = next(
                    (i for i, s in enumerate(plan.sends) if s.anchor_hours_before == hours_before),
                    0,
                )
        step = await _resolve_booking_step(
            session,
            merchant_id=cand.merchant_id,
            attempt_index=attempt_index,
            context=plan_context,
        )
        decision = decide_outbound(
            within_window=within_window,
            step=step,
            context={"contact.phone": cand.phone, "appointment.datetime": when},
        )
        if decision.mode == MODE_SKIP:
            logger.info(
                "uc02.reminder.skipped",
                appointment_id=str(cand.appointment_id),
                reason=decision.reason,
            )
            return False

        integrations = IntegrationRepository(session, kek_base64=kek)
        wa = await integrations.resolve_whatsapp(cand.wa_phone_number_id)
        if wa is None:
            logger.info(
                "uc02.reminder.no_wa_integration",
                appointment_id=str(cand.appointment_id),
            )
            return False

        # Resolve (or open) the lead's conversation so the reminder lands in the
        # inbox and the delivery callback can attach to its Message row.
        convs = ConversationRepository(session)
        conv = await convs.get_active(merchant_id=cand.merchant_id, wa_contact_phone=cand.phone)
        if conv is None:
            conv = await convs.create(
                merchant_id=cand.merchant_id,
                lead_id=cand.lead_id,
                wa_phone_number_id=cand.wa_phone_number_id,
                wa_contact_phone=cand.phone,
            )

        client = build_whatsapp_sender(
            phone_number_id=wa.phone_number_id,
            api_key=wa.api_key,
            waba_base_url=wa.waba_base_url,
        )
        try:
            await send_and_persist_decision(
                client,
                to_phone=cand.phone,
                decision=decision,
                session=session,
                conversation_id=conv.id,
                merchant_id=cand.merchant_id,
                sender_type="appointment_reminder",
            )
        finally:
            await client.close()

        # Mark only after a real send so a skipped reminder (no template yet) is
        # retried on the next tick.
        await appts.mark_reminded(cand.appointment_id, at=now)
        await analytics.emit(
            tenant_id=cand.tenant_id,
            merchant_id=cand.merchant_id,
            event_type="appointment_reminder.sent",
            subject_type="appointment",
            subject_id=cand.appointment_id,
            properties={"mode": decision.mode, "start_at": cand.start_at.isoformat()},
        )
        return True
