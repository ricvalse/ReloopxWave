"""Appointments API (UC-02) — merchant-UI reschedule / cancel.

Reads of the agenda flow directly from the frontend to Supabase under RLS; the
backend hosts only the side-effecting actions that must hit GoHighLevel. These
two endpoints run synchronously (the agenda button wants an immediate result):
they resolve the RLS-scoped appointment, call GHL, and update the local mirror
in the same transaction — sharing the exact code path the WhatsApp
reschedule/cancel handlers use (`ai_core.actions.appointment_ops`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ai_core.actions.appointment_ops import (
    AppointmentOpResult,
    cancel_appointment,
    reschedule_appointment,
)
from api.dependencies.session import CurrentContext, DBSession
from db import AppointmentRepository
from shared import PermissionDeniedError, get_logger, get_settings

router = APIRouter()
logger = get_logger(__name__)

_DEFAULT_DURATION = timedelta(minutes=30)


class RescheduleIn(BaseModel):
    start_at_iso: str = Field(description="New start time, ISO-8601.")
    end_at_iso: str | None = Field(
        default=None, description="New end time; derived from the prior duration if omitted."
    )


class AppointmentOut(BaseModel):
    id: UUID
    status: str
    start_at: datetime
    end_at: datetime | None


def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A naïve value (a datetime-local picker) is assumed to be in the
    # appointment's local tz; callers send an offset when they can.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _require_merchant(ctx: CurrentContext) -> None:
    if ctx.merchant_id is None and ctx.role != "agency_admin":
        raise PermissionDeniedError("Merchant context required", error_code="no_merchant_context")


def _raise_for_failure(result: AppointmentOpResult) -> None:
    if result.ok:
        return
    status = {
        "no_ghl_handle": 409,
        "no_ghl_integration": 409,
        "ghl_error": 502,
    }.get(result.reason or "", 500)
    raise HTTPException(status_code=status, detail=result.reason or "appointment_op_failed")


@router.post("/{appointment_id}/reschedule", response_model=AppointmentOut)
async def reschedule(
    appointment_id: UUID,
    body: RescheduleIn,
    ctx: CurrentContext,
    session: DBSession,
) -> AppointmentOut:
    _require_merchant(ctx)
    appts = AppointmentRepository(session)
    appt = await appts.get(appointment_id)  # RLS scopes; None == not yours / not found
    if appt is None:
        raise HTTPException(status_code=404, detail="appointment not found")

    new_start = _parse_iso(body.start_at_iso)
    if new_start is None:
        raise HTTPException(status_code=422, detail="invalid start_at_iso")
    if body.end_at_iso:
        new_end = _parse_iso(body.end_at_iso)
        if new_end is None:
            raise HTTPException(status_code=422, detail="invalid end_at_iso")
    else:
        duration = (appt.end_at - appt.start_at) if appt.end_at else _DEFAULT_DURATION
        new_end = new_start + duration

    settings = get_settings()
    result = await reschedule_appointment(
        session,
        appt,
        new_start=new_start,
        new_end=new_end,
        kek=settings.integrations_kek_base64,
        client_id=settings.ghl_client_id,
        client_secret=settings.ghl_client_secret,
    )
    _raise_for_failure(result)
    return AppointmentOut(
        id=appt.id, status=appt.status, start_at=appt.start_at, end_at=appt.end_at
    )


@router.post("/{appointment_id}/cancel", response_model=AppointmentOut)
async def cancel(
    appointment_id: UUID,
    ctx: CurrentContext,
    session: DBSession,
) -> AppointmentOut:
    _require_merchant(ctx)
    appts = AppointmentRepository(session)
    appt = await appts.get(appointment_id)
    if appt is None:
        raise HTTPException(status_code=404, detail="appointment not found")

    settings = get_settings()
    result = await cancel_appointment(
        session,
        appt,
        kek=settings.integrations_kek_base64,
        client_id=settings.ghl_client_id,
        client_secret=settings.ghl_client_secret,
    )
    _raise_for_failure(result)
    return AppointmentOut(
        id=appt.id, status=appt.status, start_at=appt.start_at, end_at=appt.end_at
    )
