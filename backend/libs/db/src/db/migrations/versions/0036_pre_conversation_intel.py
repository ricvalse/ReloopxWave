"""leads — optimal_send_hour + intake_score

Revision ID: 0036_pre_conv_intel
Revises: 0035_conversation_quality
Create Date: 2026-06-26

Aggiunge a `leads`:
- optimal_send_hour: ora ottimale (0-23) per l'invio di messaggi al lead
- intake_score: intent score calcolato al primo messaggio del lead (0-100)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_pre_conv_intel"
down_revision = "0035_conversation_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("optimal_send_hour", sa.SmallInteger, nullable=True))
    op.add_column("leads", sa.Column("intake_score", sa.SmallInteger, nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "intake_score")
    op.drop_column("leads", "optimal_send_hour")
