"""appointments: normalizza lo status 'confirmed' legacy a 'booked'

Revision ID: 0040_appt_status_backfill
Revises: 0039_services_biz_hours
Create Date: 2026-07-01

I tre path di prenotazione "bot_local" (GHL non connesso / senza calendario)
scrivevano erroneamente status="confirmed" invece del valore canonico "booked".
Ogni altro path (default repository, write-through GHL, reschedule/cancel e il
reconcile poll che normalizza pure il GHL "confirmed" -> "booked") usa "booked",
e i contatori dell'agenda ("Prossimi"/"Questa settimana") contano solo "booked":
gli appuntamenti locali restavano quindi visibili nel calendario ma a zero nei
conteggi. Il codice e' stato corretto; questa migrazione allinea i dati gia'
scritti. Idempotente.
"""

from __future__ import annotations

from alembic import op

revision = "0040_appt_status_backfill"
down_revision = "0039_services_biz_hours"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE appointments
           SET status = 'booked'
         WHERE status = 'confirmed'
        """
    )


def downgrade() -> None:
    # Non reversibile in modo affidabile: dopo il backfill non distinguiamo piu'
    # i record che erano 'confirmed' da quelli nativamente 'booked'. No-op.
    pass
