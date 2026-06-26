"""appointments: reminder_due_at + reminder_schedule per multipli promemoria

Revision ID: 0031_appt_reminder_due_at
Revises: 0030_ghl_sync_log
Create Date: 2026-06-26

Aggiunge due colonne a `appointments`:
  - `reminder_due_at`    TIMESTAMPTZ nullable, indicizzata: il momento in cui il
                         cron UC-02 deve inviare il prossimo promemoria. NULL = nessun
                         promemoria in sospeso (tutti inviati, o appuntamento passato).
  - `reminder_schedule`  JSONB not-null, default '[]': lista di voci
                         {due_at: ISO, sent_at: ISO|null} ordinate per due_at asc,
                         che porta la configurazione completa dei promemoria per
                         quell'appuntamento.  Il campo `reminder_due_at` è il minimo
                         unsent due_at, mantenuto denormalized per la query del cron.

Backfill:
  - Appuntamenti futuri, status='booked', nessun reminder inviato →
      reminder_schedule = [{due_at: start_at-24h, sent_at: null}]
      reminder_due_at   = start_at - 24h
  - Appuntamenti con reminder già inviato (meta->>'reminder_sent_at' IS NOT NULL) →
      reminder_schedule = [{due_at: start_at-24h, sent_at: <valore esistente>}]
      reminder_due_at   = NULL  (nessun promemoria futuro)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_appt_reminder_due_at"
down_revision = "0030_ghl_sync_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # reminder_due_at: il prossimo momento di invio (NULL = nessuno in sospeso).
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

    # reminder_schedule: lista completa delle voci, porta la storia per questo appt.
    op.add_column(
        "appointments",
        sa.Column(
            "reminder_schedule",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default="'[]'::jsonb",
        ),
    )

    # Backfill 1 — appuntamenti futuri non ancora rimandati.
    op.execute(
        """
        UPDATE appointments SET
          reminder_schedule = jsonb_build_array(
            jsonb_build_object(
              'due_at',  (start_at - INTERVAL '24 hours')::text,
              'sent_at', null
            )
          ),
          reminder_due_at = start_at - INTERVAL '24 hours'
        WHERE status = 'booked'
          AND start_at > NOW()
          AND meta->>'reminder_sent_at' IS NULL
        """
    )

    # Backfill 2 — appuntamenti con reminder già inviato.
    op.execute(
        """
        UPDATE appointments SET
          reminder_schedule = jsonb_build_array(
            jsonb_build_object(
              'due_at',  (start_at - INTERVAL '24 hours')::text,
              'sent_at', meta->>'reminder_sent_at'
            )
          ),
          reminder_due_at = NULL
        WHERE meta->>'reminder_sent_at' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("appointments", "reminder_schedule")
    op.drop_index("ix_appointments_reminder_due_at", table_name="appointments")
    op.drop_column("appointments", "reminder_due_at")
