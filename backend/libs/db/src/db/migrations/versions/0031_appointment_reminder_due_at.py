"""appointments.reminder_due_at — tempo di invio promemoria configurabile

Revision ID: 0031_appt_reminder_due_at
Revises: 0030_ghl_sync_log
Create Date: 2026-06-26

Aggiunge `reminder_due_at` alla tabella `appointments`: calcolato come
`start_at - booking.reminder_lead_hours` al momento della prenotazione o
del sync GHL. Il cron UC-02 usa questo campo invece di un finestra fissa
di 24 ore, consentendo a ogni merchant di configurare il tempo di anticipo
del promemoria WhatsApp.

Backfill: le righe esistenti con status='booked' e senza reminder_sent_at
ricevono `reminder_due_at = start_at - INTERVAL '24 hours'` (default).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_appt_reminder_due_at"
down_revision = "0030_ghl_sync_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column("reminder_due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_appointments_reminder_due_at",
        "appointments",
        ["reminder_due_at"],
        postgresql_where=sa.text("reminder_due_at IS NOT NULL"),
    )
    # Backfill: row esistenti con appuntamento futuro e reminder non ancora inviato.
    op.execute(
        """
        UPDATE appointments
        SET reminder_due_at = start_at - INTERVAL '24 hours'
        WHERE status = 'booked'
          AND meta->>'reminder_sent_at' IS NULL
          AND start_at > NOW()
        """
    )


def downgrade() -> None:
    op.drop_index("ix_appointments_reminder_due_at", table_name="appointments")
    op.drop_column("appointments", "reminder_due_at")
