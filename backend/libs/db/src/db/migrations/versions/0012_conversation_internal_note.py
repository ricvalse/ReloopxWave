"""conversations.internal_note for the inbox detail panel

Revision ID: 0012_conversation_internal_note
Revises: 0011_auto_reply
Create Date: 2026-05-28

Free-text internal note an agent jots about a conversation from the inbox
detail panel (right rail). Per-thread, not per-lead: a lead can span several
threads, but the note is about the live conversation in front of the agent.
Nullable text, no default — an empty/cleared note is stored as NULL.

`conversations` is already in RLS_TABLES_MERCHANT_SCOPED with a
`merchant_isolation_conversations` policy (0001_initial), so this column is
covered for both the direct-Supabase read and the PATCH /notes write without
any policy change. The table is also published to `supabase_realtime` with
REPLICA IDENTITY FULL (0008), so note UPDATEs broadcast the full row and the
list subscription reconciles optimistic note saves automatically.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_conversation_internal_note"
down_revision: str | Sequence[str] | None = "0011_auto_reply"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("internal_note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "internal_note")
