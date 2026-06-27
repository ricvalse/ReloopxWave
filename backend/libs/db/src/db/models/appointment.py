from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, uuid_pk


class Appointment(Base, TimestampMixin):
    """Local mirror of a GoHighLevel calendar appointment (UC-02).

    GHL stays the system of record; this table is a derived read-model kept in
    sync by (a) write-through when the bot books/reschedules/cancels and
    (b) a reconcile poll (GHL sends no appointment webhooks). `ghl_appointment_id`
    is the join handle back to the GHL event — without it reschedule/cancel and
    reconciliation are impossible, which is why it is captured at booking time
    (the live booking flow used to drop it). `source` records who created the
    row: `bot` (our write-through), `merchant` (the agenda UI), or `ghl` (a row
    first seen via the reconcile poll, i.e. created manually inside GHL).
    """

    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "ghl_appointment_id", name="uq_appointments_merchant_ghl_event"
        ),
        Index("ix_appointments_merchant_start", "merchant_id", "start_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable: the reconcile poll may surface appointments for GHL contacts we
    # don't track as leads, so the mirror must hold an appointment with no lead.
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ghl_appointment_id: Mapped[str | None] = mapped_column(String(120))
    ghl_contact_id: Mapped[str | None] = mapped_column(String(120))
    calendar_id: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str | None] = mapped_column(String(300))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The merchant-local tz the slot was resolved in, so the agenda renders the
    # wall-clock the lead actually agreed to (the booking flow bakes tz into the
    # stored offset, but keeping the name avoids re-deriving it).
    tz_name: Mapped[str | None] = mapped_column(String(64))
    # Quale servizio è stato prenotato (NULL per appuntamenti legacy / da GHL).
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("services.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="booked")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="bot")
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # UC-02 multi-reminder support.
    # `reminder_due_at` — denormalized: il prossimo due_at non ancora inviato.
    # NULL significa nessun promemoria in sospeso (tutti inviati o appt. passato).
    reminder_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # `reminder_schedule` — lista ordinata {due_at: ISO, sent_at: ISO|null}.
    # Costruita a booking/sync time dalla configurazione del merchant;
    # aggiornata da mark_reminded() a ogni invio.
    reminder_schedule: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
