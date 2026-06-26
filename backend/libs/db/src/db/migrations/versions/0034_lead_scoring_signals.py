"""leads — behavioral scoring signals + score decay

Revision ID: 0034_lead_scoring_signals
Revises: 0033_kb_intelligence
Create Date: 2026-06-26

Aggiunge a `leads`:
- avg_response_latency_seconds: latenza media di risposta del lead (secondi)
- avg_message_length_chars: lunghezza media dei messaggi in ingresso
- read_receipt_ratio: frazione di messaggi letti (0.0 – 1.0)
- effective_score: score cumulativo con decay temporale (calcolato dal cron)
- velocity_flag: 'high' | 'normal' | 'stalled' basato su progressione FSM
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_lead_scoring_signals"
down_revision = "0033_kb_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("avg_response_latency_seconds", sa.Integer, nullable=True))
    op.add_column("leads", sa.Column("avg_message_length_chars", sa.Integer, nullable=True))
    op.add_column("leads", sa.Column("read_receipt_ratio", sa.Float, nullable=True))
    op.add_column("leads", sa.Column("effective_score", sa.Float, nullable=True))
    op.add_column("leads", sa.Column("velocity_flag", sa.String(16), nullable=True))


def downgrade() -> None:
    for col in ["velocity_flag", "effective_score", "read_receipt_ratio",
                "avg_message_length_chars", "avg_response_latency_seconds"]:
        op.drop_column("leads", col)
