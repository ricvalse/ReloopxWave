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

from sqlalchemy.ext.asyncio import AsyncSession

from db import GHLMarketplaceRepository, IntegrationRepository, session_scope
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
) -> AppointmentOpResult:
    """Move `appointment` to a new slot in GHL, then mirror it locally."""
    if not appointment.ghl_appointment_id:
        return AppointmentOpResult(False, "no_ghl_handle")
    client = await _build_client(
        session,
        merchant_id=appointment.merchant_id,
        kek=kek,
        client_id=client_id,
        client_secret=client_secret,
    )
    if client is None:
        return AppointmentOpResult(False, "no_ghl_integration")
    try:
        await client.reschedule_appointment(
            appointment.ghl_appointment_id,
            slot_start_iso=new_start.isoformat(),
            slot_end_iso=new_end.isoformat(),
        )
    except IntegrationError as e:
        logger.warning("appointment.reschedule_failed", error=str(e))
        return AppointmentOpResult(False, "ghl_error")
    finally:
        await client.close()

    appointment.start_at = new_start
    appointment.end_at = new_end
    appointment.status = "booked"
    return AppointmentOpResult(True)


async def cancel_appointment(
    session: AsyncSession,
    appointment: Appointment,
    *,
    kek: str,
    client_id: str,
    client_secret: str,
) -> AppointmentOpResult:
    """Cancel `appointment` in GHL, then mark the mirror row cancelled."""
    if not appointment.ghl_appointment_id:
        return AppointmentOpResult(False, "no_ghl_handle")
    client = await _build_client(
        session,
        merchant_id=appointment.merchant_id,
        kek=kek,
        client_id=client_id,
        client_secret=client_secret,
    )
    if client is None:
        return AppointmentOpResult(False, "no_ghl_integration")
    try:
        await client.cancel_appointment(appointment.ghl_appointment_id)
    except IntegrationError as e:
        logger.warning("appointment.cancel_failed", error=str(e))
        return AppointmentOpResult(False, "ghl_error")
    finally:
        await client.close()

    appointment.status = "cancelled"
    return AppointmentOpResult(True)
