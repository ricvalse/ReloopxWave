from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Appointment, Conversation, Lead, Merchant


@dataclass(slots=True, frozen=True)
class AppointmentReminderCandidate:
    appointment_id: UUID
    merchant_id: UUID
    tenant_id: UUID
    lead_id: UUID
    phone: str
    wa_phone_number_id: str
    last_inbound_at: datetime | None
    start_at: datetime
    tz_name: str | None


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

    async def list_due_for_reminder(
        self, *, now: datetime, horizon_end: datetime, limit: int = 500
    ) -> list[AppointmentReminderCandidate]:
        """Cross-tenant scan of booked appointments starting within
        [now, horizon_end] that haven't had a reminder sent yet (UC-02).

        Joins the lead (phone) + merchant (tenant) + the lead's most-recent
        conversation (the channel to message on + the 24h-window anchor).
        Appointments with no linked lead (reconcile-poll rows) are skipped — we
        have no one to message. Idempotency is the `meta.reminder_sent_at` marker,
        stamped by `mark_reminded` only on a real send.
        """
        latest_conv = (
            select(
                Conversation.lead_id,
                Conversation.wa_phone_number_id,
                Conversation.last_inbound_at,
            )
            .distinct(Conversation.lead_id)
            .order_by(Conversation.lead_id, Conversation.last_message_at.desc())
            .subquery()
        )
        stmt = (
            select(
                Appointment.id,
                Appointment.merchant_id,
                Merchant.tenant_id,
                Appointment.lead_id,
                Lead.phone,
                latest_conv.c.wa_phone_number_id,
                latest_conv.c.last_inbound_at,
                Appointment.start_at,
                Appointment.tz_name,
            )
            .join(Lead, Lead.id == Appointment.lead_id)
            .join(Merchant, Merchant.id == Appointment.merchant_id)
            .join(latest_conv, latest_conv.c.lead_id == Appointment.lead_id)
            .where(
                Appointment.status == "booked",
                Appointment.start_at >= now,
                Appointment.start_at <= horizon_end,
                Appointment.meta["reminder_sent_at"].astext.is_(None),
            )
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).mappings()
        candidates: list[AppointmentReminderCandidate] = []
        for row in rows:
            if not row["phone"] or not row["wa_phone_number_id"]:
                continue
            candidates.append(
                AppointmentReminderCandidate(
                    appointment_id=row["id"],
                    merchant_id=row["merchant_id"],
                    tenant_id=row["tenant_id"],
                    lead_id=row["lead_id"],
                    phone=row["phone"],
                    wa_phone_number_id=row["wa_phone_number_id"],
                    last_inbound_at=row["last_inbound_at"],
                    start_at=row["start_at"],
                    tz_name=row["tz_name"],
                )
            )
        return candidates

    async def mark_reminded(self, appointment_id: UUID, *, at: datetime) -> None:
        """Stamp the reminder marker so the appointment isn't reminded again."""
        appt = await self._session.get(Appointment, appointment_id)
        if appt is None:
            return
        appt.meta = {**(appt.meta or {}), "reminder_sent_at": at.isoformat()}

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
