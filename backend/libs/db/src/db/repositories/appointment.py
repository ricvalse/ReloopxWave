from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Appointment, Conversation, Lead, Merchant

# ---------------------------------------------------------------------------
# Pure helpers — non dipendono dalla sessione DB, usate da booking.py e
# appointment_sync.py per costruire/interrogare il reminder_schedule.
# ---------------------------------------------------------------------------


def build_reminder_schedule(
    start_at: datetime, lead_hours: list[int]
) -> list[dict[str, str | None]]:
    """Costruisce [{due_at: ISO, sent_at: None}, ...] ordinata per due_at asc.

    - De-duplica le ore (set).
    - Ordina in modo che le ore maggiori (reminder più lontani dall'appuntamento)
      vengano prime — cioè due_at asc.
    """
    seen: set[int] = set()
    entries: list[dict[str, str | None]] = []
    for h in sorted(set(lead_hours), reverse=True):  # largest h → earliest due_at
        if h in seen:
            continue
        seen.add(h)
        entries.append(
            {
                "due_at": (start_at - timedelta(hours=h)).isoformat(),
                "sent_at": None,
            }
        )
    return entries  # già in ordine asc di due_at


def next_reminder_due(schedule: list[dict[str, str | None]]) -> datetime | None:
    """Ritorna il due_at della prima voce non inviata, o None se tutte inviate."""
    for entry in schedule:
        if entry.get("sent_at") is None:
            dt = datetime.fromisoformat(entry["due_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
    return None


# ---------------------------------------------------------------------------
# Dataclass di output per list_due_for_reminder
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


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
        reminder_schedule: list[dict[str, str | None]] | None = None,
        reminder_due_at: datetime | None = None,
    ) -> Appointment:
        """Persist the local mirror row for a freshly-created GHL appointment.

        Write-through: called right after `create_booking` succeeds so the
        GHL-returned `ghl_appointment_id` (otherwise dropped) survives as the
        join handle for later reschedule/cancel/reconcile.

        `reminder_schedule` e `reminder_due_at` sono costruiti dal chiamante
        con `build_reminder_schedule` / `next_reminder_due` e passati qui.
        """
        kwargs: dict = {
            "merchant_id": merchant_id,
            "lead_id": lead_id,
            "ghl_appointment_id": ghl_appointment_id,
            "ghl_contact_id": ghl_contact_id,
            "calendar_id": calendar_id,
            "start_at": start_at,
            "end_at": end_at,
            "tz_name": tz_name,
            "title": title,
            "status": status,
            "source": source,
        }
        if reminder_schedule is not None:
            kwargs["reminder_schedule"] = reminder_schedule
        if reminder_due_at is not None:
            kwargs["reminder_due_at"] = reminder_due_at
        appt = Appointment(**kwargs)
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
        reminder_schedule: list[dict[str, str | None]] | None = None,
        reminder_due_at: datetime | None = None,
    ) -> None:
        """Idempotent upsert from the reconcile poll, keyed on
        (merchant_id, ghl_appointment_id).

        On conflict it refreshes only the mutable, GHL-owned fields (time,
        status, title, calendar, contact) so a reschedule/cancel made manually
        inside GHL is reflected locally. It deliberately does NOT overwrite
        `lead_id` or `source`: a row first created by the bot's write-through
        keeps its lead linkage and `source='bot'` even after a later poll that
        can't resolve the lead.

        Il reminder_schedule è resettato solo se start_at cambia (reschedule):
        se l'appuntamento non si sposta, manteniamo lo schedule esistente
        (inclusi gli eventuali promemoria già inviati).
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
            "reminder_schedule": reminder_schedule if reminder_schedule is not None else [],
            "reminder_due_at": reminder_due_at,
        }
        ins = pg_insert(Appointment).values(**insert_values)
        tbl = Appointment.__table__.c
        stmt = ins.on_conflict_do_update(
            constraint="uq_appointments_merchant_ghl_event",
            set_={
                "start_at": ins.excluded.start_at,
                "end_at": ins.excluded.end_at,
                "status": ins.excluded.status,
                "title": ins.excluded.title,
                "calendar_id": ins.excluded.calendar_id,
                "ghl_contact_id": ins.excluded.ghl_contact_id,
                "updated_at": func.now(),
                # Resetta il reminder_schedule solo se start_at cambia (reschedule).
                # Se l'appuntamento è invariato, preserva lo schedule esistente.
                "reminder_schedule": case(
                    (tbl.start_at != ins.excluded.start_at, ins.excluded.reminder_schedule),
                    else_=tbl.reminder_schedule,
                ),
                "reminder_due_at": case(
                    (tbl.start_at != ins.excluded.start_at, ins.excluded.reminder_due_at),
                    else_=tbl.reminder_due_at,
                ),
            },
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
        self, *, now: datetime, limit: int = 500
    ) -> list[AppointmentReminderCandidate]:
        """Cross-tenant scan degli appuntamenti con reminder_due_at <= now (UC-02).

        Usa il campo denormalized `reminder_due_at` invece del vecchio filtro su
        `start_at` + finestra fissa, consentendo schedule multi-reminder
        configurabili per merchant.

        Joins the lead (phone) + merchant (tenant) + the lead's most-recent
        conversation (the channel to message on + the 24h-window anchor).
        Appointments with no linked lead (reconcile-poll rows) are skipped — we
        have no one to message.
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
                Appointment.reminder_due_at.isnot(None),
                Appointment.reminder_due_at <= now,
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
        """Marca come inviata la prima voce non inviata con due_at <= at.

        Aggiorna `reminder_schedule` (new list per trigger dirty-tracking JSONB)
        e ricalcola `reminder_due_at` dal nuovo schedule.
        """
        appt = await self._session.get(Appointment, appointment_id)
        if appt is None:
            return
        at_iso = at.isoformat()
        # Crea una nuova lista (e nuovi dict) per triggering dirty-tracking SQLAlchemy.
        new_schedule: list[dict[str, str | None]] = []
        marked = False
        for entry in appt.reminder_schedule or []:
            new_entry = dict(entry)
            if not marked and new_entry.get("sent_at") is None:
                due_str = new_entry.get("due_at")
                if due_str:
                    due = datetime.fromisoformat(due_str)
                    if due.tzinfo is None:
                        due = due.replace(tzinfo=UTC)
                    if due <= at:
                        new_entry["sent_at"] = at_iso
                        marked = True
            new_schedule.append(new_entry)
        appt.reminder_schedule = new_schedule
        appt.reminder_due_at = next_reminder_due(new_schedule)

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
