"""structured human-handoff state on conversations

Revision ID: 0025_conversation_handoff
Revises: 0024_bot_corrections
Create Date: 2026-06-19

Adds the "Amalia-style" handoff columns so the inbox can triage, assign and
time-box human takeover:

  * `ai_disabled_until` — soft-pause with auto-resume (phone-echo 2h, operator
    "disable AI for N"); the bot stays silent without flipping `auto_reply`.
  * `assigned_to` / `handoff_reason` / `handoff_summary` / `handoff_at` /
    `handoff_resolved_at` — who owns the thread, why, the AI's brief for the
    operator, and the escalation lifecycle timestamps.

All nullable, no backfill. RLS already covers `conversations` (0001/0014); no
new policy needed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_conversation_handoff"
down_revision: str | Sequence[str] | None = "0024_bot_corrections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "ai_disabled_until",
    "assigned_to",
    "handoff_reason",
    "handoff_summary",
    "handoff_at",
    "handoff_resolved_at",
)


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("ai_disabled_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("conversations", sa.Column("assigned_to", sa.String(255), nullable=True))
    op.add_column("conversations", sa.Column("handoff_reason", sa.String(255), nullable=True))
    op.add_column("conversations", sa.Column("handoff_summary", sa.Text, nullable=True))
    op.add_column(
        "conversations", sa.Column("handoff_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "conversations",
        sa.Column("handoff_resolved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    for col in reversed(_COLUMNS):
        op.drop_column("conversations", col)
