"""Shared reschedule/cancel operations against GHL + the local mirror (UC-02).

One code path used by BOTH the WhatsApp action handlers (`RescheduleSlotHandler`
/ `CancelSlotHandler`) and the merchant-UI FastAPI endpoints, so the GHL write,
the write-through mirror update, and the token-rotation persistence live in
exactly one place. GHL stays the system of record; on success we update the
local row in the caller's session (the caller commits).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from db import (
    AnalyticsRepository,
    GHLMarketplaceRepository,
    GhlSyncRepository,
    IntegrationRepository,
    build_reminder_schedule,
    next_reminder_due,
    session_scope,
)
from db.models import Appointment
from integrations.ghl.client import GHLClient, GHLTokenBundle
from shared import IntegrationError, get_logger

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class AppointmentOpResult:
    ok: bool
    reason: str | None = None


async def _build_client(
    session: AsyncSession,
    *,
    merchant_id: object,
    kek: str,
    client_id: str,
    client_secret: str,
) -> GHLClient | None:
    ghl = await IntegrationRepository(session, kek_base64=kek).resolve_ghl(merchant_id)  # type: ignore[arg-type]
    if ghl is None:
        return None

    async def _persist(bundle: GHLTokenBundle) -> None:
        # Rotated refresh token must survive even if a later call in this run
        # fails — own committed transaction, same gotcha the booking flow guards.
        if not bundle.location_id:
            return
        async with session_scope() as token_session:
            await GHLMarketplaceRepository(token_session, kek_base64=kek).set_location_token(
                location_id=bundle.location_id,
                access_token=bundle.access_token,
                refresh_token=bundle.refresh_token,
                expires_at=bundle.expires_at,
            )

    return GHLClient(
        token_bundle=GHLTokenBundle(
            access_token=ghl.access_token,
            refresh_token=ghl.refresh_token,
            expires_at=ghl.expires_at,
            location_id=ghl.location_id,
        ),
        client_id=client_id,
        client_secret=client_secret,
        on_token_refresh=_persist,
    )


async def reschedule_appointment(
    session: AsyncSession,
    appointment: Appointment,
    *,
    new_start: datetime,
    new_end: datetime,
    kek: str,
    client_id: str,
    client_secret: str,
    tenant_id: UUID | None = None,
    conversation_id: UUID | None = None,
) -> AppointmentOpResult:
    """Move `appointment` to a new slot in GHL, then mirror it locally."""
    if not appointment.ghl_appointment_id:
        # Appuntamento locale (senza GHL): aggiorna solo il mirror.
        appointment.start_at = new_start
        appointment.end_at = new_end
        appointment.status = "booked"
        r_schedule = build_reminder_schedule(new_start, [24])
        appointment.reminder_schedule = r_schedule
        appointment.reminder_due_at = next_reminder_due(r_schedule)
        if tenant_id is not None:
            await _emit_analytics(
                session, tenant_id=tenant_id, merchant_id=appointment.merchant_id,
                lead_id=appointment.lead_id, conversation_id=conversation_id,
                event_type="booking.rescheduled",
                properties={"local_only": True, "new_start": new_start.isoformat()},
            )
        return AppointmentOpResult(True, "local_only")
    client = await _build_client(
        session,
        merchant_id=appointment.merchant_id,
        kek=kek,
        client_id=client_id,
        client_secret=client_secret,
    )
    if client is None:
        return AppointmentOpResult(False, "no_ghl_integration")

    payload: dict[str, Any] = {
        "ghl_appointment_id": appointment.ghl_appointment_id,
        "new_start": new_start.isoformat(),
        "new_end": new_end.isoformat(),
    }
    try:
        await client.reschedule_appointment(
            appointment.ghl_appointment_id,
            slot_start_iso=new_start.isoformat(),
            slot_end_iso=new_end.isoformat(),
        )
    except IntegrationError as e:
        logger.warning("appointment.reschedule_failed", error=str(e))
        await _emit_sync(
            session, tenant_id=tenant_id, merchant_id=appointment.merchant_id,
            lead_id=appointment.lead_id, conversation_id=conversation_id,
            operation="booking.rescheduled", ghl_entity_id=appointment.ghl_appointment_id,
            status="error", error_detail=str(e), payload=payload,
        )
        return AppointmentOpResult(False, "ghl_error")
    finally:
        await client.close()

    appointment.start_at = new_start
    appointment.end_at = new_end
    appointment.status = "booked"

    await _emit_sync(
        session, tenant_id=tenant_id, merchant_id=appointment.merchant_id,
        lead_id=appointment.lead_id, conversation_id=conversation_id,
        operation="booking.rescheduled", ghl_entity_id=appointment.ghl_appointment_id,
        payload=payload, result={"new_start": new_start.isoformat()},
    )
    if tenant_id is not None:
        await _emit_analytics(
            session, tenant_id=tenant_id, merchant_id=appointment.merchant_id,
            lead_id=appointment.lead_id, conversation_id=conversation_id,
            event_type="booking.rescheduled",
            properties={
                "ghl_appointment_id": appointment.ghl_appointment_id,
                "new_start": new_start.isoformat(),
            },
        )
    return AppointmentOpResult(True)


async def cancel_appointment(
    session: AsyncSession,
    appointment: Appointment,
    *,
    kek: str,
    client_id: str,
    client_secret: str,
    tenant_id: UUID | None = None,
    conversation_id: UUID | None = None,
) -> AppointmentOpResult:
    """Cancel `appointment` in GHL, then mark the mirror row cancelled."""
    if not appointment.ghl_appointment_id:
        # Appuntamento locale (senza GHL): aggiorna solo il mirror.
        appointment.status = "cancelled"
        appointment.reminder_due_at = None
        if tenant_id is not None:
            await _emit_analytics(
                session, tenant_id=tenant_id, merchant_id=appointment.merchant_id,
                lead_id=appointment.lead_id, conversation_id=conversation_id,
                event_type="booking.cancelled",
                properties={"local_only": True},
            )
        return AppointmentOpResult(True, "local_only")
    client = await _build_client(
        session,
        merchant_id=appointment.merchant_id,
        kek=kek,
        client_id=client_id,
        client_secret=client_secret,
    )
    if client is None:
        return AppointmentOpResult(False, "no_ghl_integration")

    payload: dict[str, Any] = {"ghl_appointment_id": appointment.ghl_appointment_id}
    try:
        await client.cancel_appointment(appointment.ghl_appointment_id)
    except IntegrationError as e:
        logger.warning("appointment.cancel_failed", error=str(e))
        await _emit_sync(
            session, tenant_id=tenant_id, merchant_id=appointment.merchant_id,
            lead_id=appointment.lead_id, conversation_id=conversation_id,
            operation="booking.cancelled", ghl_entity_id=appointment.ghl_appointment_id,
            status="error", error_detail=str(e), payload=payload,
        )
        return AppointmentOpResult(False, "ghl_error")
    finally:
        await client.close()

    appointment.status = "cancelled"

    await _emit_sync(
        session, tenant_id=tenant_id, merchant_id=appointment.merchant_id,
        lead_id=appointment.lead_id, conversation_id=conversation_id,
        operation="booking.cancelled", ghl_entity_id=appointment.ghl_appointment_id,
        payload=payload,
    )
    if tenant_id is not None:
        await _emit_analytics(
            session, tenant_id=tenant_id, merchant_id=appointment.merchant_id,
            lead_id=appointment.lead_id, conversation_id=conversation_id,
            event_type="booking.cancelled",
            properties={"ghl_appointment_id": appointment.ghl_appointment_id},
        )
    return AppointmentOpResult(True)


async def _emit_sync(
    session: AsyncSession,
    *,
    tenant_id: UUID | None,
    merchant_id: Any,
    lead_id: Any,
    conversation_id: Any,
    operation: str,
    ghl_entity_id: str | None = None,
    status: str = "success",
    error_detail: str | None = None,
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    if tenant_id is None:
        return
    try:
        await GhlSyncRepository(session).emit(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
            operation=operation,
            ghl_entity_type="appointment",
            ghl_entity_id=ghl_entity_id,
            status=status,
            error_detail=error_detail,
            payload=payload,
            result=result,
        )
    except Exception as e:
        logger.warning("ghl_sync.emit_failed", error=str(e), operation=operation)


async def _emit_analytics(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    merchant_id: Any,
    lead_id: Any,
    conversation_id: Any,
    event_type: str,
    properties: dict[str, Any] | None = None,
) -> None:
    try:
        await AnalyticsRepository(session).emit(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            event_type=event_type,
            subject_type="lead" if lead_id else "appointment",
            subject_id=lead_id,
            properties={
                **(properties or {}),
                "conversation_id": str(conversation_id) if conversation_id else None,
            },
        )
    except Exception as e:
        logger.warning("analytics.emit_failed", error=str(e), event_type=event_type)
