"""appointments: aggiungi colonna reminder_schedule se mancante

Revision ID: 0038_appt_reminder_schedule
Revises: 0037_kb_gaps_rls
Create Date: 2026-06-27

La migrazione 0031 fu applicata parzialmente via Supabase MCP:
reminder_due_at esiste ma reminder_schedule non fu aggiunta.
Questa migrazione aggiunge la colonna mancante in modo idempotente
(IF NOT EXISTS) e fa il backfill allineato a quello di 0031.
"""

from __future__ import annotations

from alembic import op

revision = "0038_appt_reminder_schedule"
down_revision = "0037_kb_gaps_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE appointments
          ADD COLUMN IF NOT EXISTS reminder_schedule JSONB NOT NULL DEFAULT '[]'::jsonb
        """
    )

    # Backfill 1 — appuntamenti futuri senza reminder inviato.
    op.execute(
        """
        UPDATE appointments SET
          reminder_schedule = jsonb_build_array(
            jsonb_build_object(
              'due_at',  (start_at - INTERVAL '24 hours')::text,
              'sent_at', null
            )
          )
        WHERE status = 'booked'
          AND start_at > NOW()
          AND meta->>'reminder_sent_at' IS NULL
          AND reminder_schedule = '[]'::jsonb
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
          )
        WHERE meta->>'reminder_sent_at' IS NOT NULL
          AND reminder_schedule = '[]'::jsonb
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS reminder_schedule")
