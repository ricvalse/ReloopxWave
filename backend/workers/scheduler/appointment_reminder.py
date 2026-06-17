"""UC-02 — appointment reminders.

Sends a WhatsApp reminder ahead of a booked appointment. The local
`appointments` mirror (write-through at booking + reconcile poll) is the source
of truth for upcoming slots, so this job just scans it for appointments starting
within the reminder lead time and sends one reminder each.

A reminder for an appointment booked days ago is almost always OUTSIDE the 24h
window, so — like reactivation — it goes through `decide_outbound` and is sent
only when an approved `booking_reminder` template (or the within-window free
text) is available; otherwise it is skipped and retried on the next tick until
the slot passes. Idempotency is the `appointments.meta.reminder_sent_at` marker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from db import (
    FLOW_BOOKING_REMINDER,
    AnalyticsRepository,
    AppointmentReminderCandidate,
    AppointmentRepository,
    FlowRepository,
    IntegrationRepository,
    TenantContext,
    session_scope,
    tenant_session,
)
from integrations.whatsapp.factory import build_whatsapp_sender
from shared import get_logger
from workers.outbound import MODE_SKIP, decide_outbound, is_within_24h, send_decision

logger = get_logger(__name__)

# How far ahead of the slot we send the reminder.
REMINDER_LEAD = timedelta(hours=24)


async def send_appointment_reminders(ctx: dict[str, Any]) -> dict[str, Any]:
    settings = ctx["settings"]
    now = datetime.now(tz=UTC)
    candidates = await _scan(now=now, horizon_end=now + REMINDER_LEAD)
    logger.info("uc02.reminder.scan", count=len(candidates))

    sent = 0
    for cand in candidates:
        if await _maybe_send(cand, now=now, kek=settings.integrations_kek_base64):
            sent += 1
    return {"candidates": len(candidates), "sent": sent}


async def _scan(*, now: datetime, horizon_end: datetime) -> list[AppointmentReminderCandidate]:
    async with session_scope() as session:
        return await AppointmentRepository(session).list_due_for_reminder(
            now=now, horizon_end=horizon_end
        )


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

        when = _format_slot(cand.start_at, cand.tz_name)
        fallback_text = f"Promemoria: hai un appuntamento {when}. A presto!"

        step = await FlowRepository(session).resolve_step(
            merchant_id=cand.merchant_id, key=FLOW_BOOKING_REMINDER, step_index=0
        )
        decision = decide_outbound(
            within_window=is_within_24h(cand.last_inbound_at, now),
            fallback_text=fallback_text,
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

        client = build_whatsapp_sender(
            phone_number_id=wa.phone_number_id,
            api_key=wa.api_key,
            waba_base_url=wa.waba_base_url,
        )
        try:
            await send_decision(client, to_phone=cand.phone, decision=decision)
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
