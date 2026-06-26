"""conversations.context_summary — context compressor memory block

Revision ID: 0035_conversation_quality
Revises: 0034_lead_scoring_signals
Create Date: 2026-06-26

Aggiunge `context_summary` JSONB a `conversations`: contiene il riepilogo
dei turni compressi (text, compressed_turns, compressed_at).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035_conversation_quality"
down_revision = "0034_lead_scoring_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("context_summary", sa.dialects.postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "context_summary")
