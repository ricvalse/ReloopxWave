from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Appointment


class AppointmentRepository:
    """Read/write access to the local GHL-appointment mirror (UC-02).

    Tier 1 covers the write-through path (`record_booking`) plus the reads the
    isolation tests and the merchant agenda need. Tier 2 adds `upsert_by_ghl_id`
    for the reconcile poll.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_booking(
        self,
        *,
        merchant_id: UUID,
        lead_id: UUID | None,
        ghl_appointment_id: str | None,
        ghl_contact_id: str | None,
        calendar_id: str | None,
        start_at: datetime,
        end_at: datetime | None,
        tz_name: str | None,
        title: str | None = None,
        status: str = "booked",
        source: str = "bot",
    ) -> Appointment:
        """Persist the local mirror row for a freshly-created GHL appointment.

        Write-through: called right after `create_booking` succeeds so the
        GHL-returned `ghl_appointment_id` (otherwise dropped) survives as the
        join handle for later reschedule/cancel/reconcile.
        """
        appt = Appointment(
            merchant_id=merchant_id,
            lead_id=lead_id,
            ghl_appointment_id=ghl_appointment_id,
            ghl_contact_id=ghl_contact_id,
            calendar_id=calendar_id,
            start_at=start_at,
            end_at=end_at,
            tz_name=tz_name,
            title=title,
            status=status,
            source=source,
        )
        self._session.add(appt)
        await self._session.flush()
        return appt

    async def upsert_by_ghl_id(
        self,
        *,
        merchant_id: UUID,
        ghl_appointment_id: str,
        start_at: datetime,
        end_at: datetime | None,
        lead_id: UUID | None = None,
        ghl_contact_id: str | None = None,
        calendar_id: str | None = None,
        title: str | None = None,
        tz_name: str | None = None,
        status: str = "booked",
        source: str = "ghl",
    ) -> None:
        """Idempotent upsert from the reconcile poll, keyed on
        (merchant_id, ghl_appointment_id).

        On conflict it refreshes only the mutable, GHL-owned fields (time,
        status, title, calendar, contact) so a reschedule/cancel made manually
        inside GHL is reflected locally. It deliberately does NOT overwrite
        `lead_id` or `source`: a row first created by the bot's write-through
        keeps its lead linkage and `source='bot'` even after a later poll that
        can't resolve the lead.
        """
        insert_values: dict[str, object | None] = {
            "merchant_id": merchant_id,
            "ghl_appointment_id": ghl_appointment_id,
            "lead_id": lead_id,
            "ghl_contact_id": ghl_contact_id,
            "calendar_id": calendar_id,
            "title": title,
            "start_at": start_at,
            "end_at": end_at,
            "tz_name": tz_name,
            "status": status,
            "source": source,
        }
        stmt = (
            pg_insert(Appointment)
            .values(**insert_values)
            .on_conflict_do_update(
                constraint="uq_appointments_merchant_ghl_event",
                set_={
                    "start_at": start_at,
                    "end_at": end_at,
                    "status": status,
                    "title": title,
                    "calendar_id": calendar_id,
                    "ghl_contact_id": ghl_contact_id,
                    "updated_at": func.now(),
                },
            )
        )
        await self._session.execute(stmt)

    async def get(self, appointment_id: UUID) -> Appointment | None:
        return await self._session.get(Appointment, appointment_id)

    async def list_for_merchant(self, merchant_id: UUID, *, limit: int = 200) -> list[Appointment]:
        """Most-recent-first appointments for a merchant (agenda + isolation)."""
        stmt = (
            select(Appointment)
            .where(Appointment.merchant_id == merchant_id)
            .order_by(Appointment.start_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_upcoming_for_lead(
        self, *, merchant_id: UUID, lead_id: UUID, now: datetime, limit: int = 5
    ) -> list[Appointment]:
        """Soonest-first upcoming, still-booked appointments for one lead.

        Powers the WhatsApp reschedule/cancel flow: the bot knows the lead, not
        the appointment id. Cancelled/past rows are excluded so the bot acts on
        a live appointment (and can detect ambiguity when more than one is due).
        """
        stmt = (
            select(Appointment)
            .where(
                Appointment.merchant_id == merchant_id,
                Appointment.lead_id == lead_id,
                Appointment.start_at >= now,
                Appointment.status == "booked",
            )
            .order_by(Appointment.start_at.asc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())
