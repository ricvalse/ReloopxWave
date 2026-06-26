"""kb_chunks.last_updated_at + kb_gaps — RAG intelligence

Revision ID: 0033_kb_intelligence
Revises: 0032_conv_state_machine
Create Date: 2026-06-26

Aggiunge:
- kb_chunks.last_updated_at: usato per il freshness decay nel ranking vettoriale
- kb_gaps: domande dei lead che non hanno trovato risposta nella KB
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_kb_intelligence"
down_revision = "0032_conv_state_machine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kb_chunks",
        sa.Column(
            "last_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "kb_gaps",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("merchant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("frequency", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_kb_gaps_merchant_resolved", "kb_gaps", ["merchant_id", "resolved"])


def downgrade() -> None:
    op.drop_index("ix_kb_gaps_merchant_resolved", table_name="kb_gaps")
    op.drop_table("kb_gaps")
    op.drop_column("kb_chunks", "last_updated_at")
