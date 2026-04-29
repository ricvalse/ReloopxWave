"""messages: status, direction, client_message_id, delivered/read/failed timestamps

Revision ID: 0009_messages_status_ticks
Revises: 0008_realtime_publish_messages
Create Date: 2026-04-28

Backs the WhatsApp-style composer end-to-end pipeline: optimistic insert
(`status='pending'`, `client_message_id=<uuid>`), worker dispatch
(`status='sent'`, `wa_message_id=<meta id>`), webhook status callbacks
(`delivered_at`, `read_at`), and terminal failure (`status='failed'`,
`error`).

`direction` is added as a denormalised column derived from `role` so the
frontend can filter without a CASE expression. Backfill maps:
  user      -> 'in'
  assistant -> 'out'   (existing bot replies)
  agent     -> 'out'   (human replies, new in this migration)

`client_message_id` lets the FastAPI insert be idempotent for retried
POSTs from the composer. Unique only when present (partial index) to
avoid forcing nulls onto inbound/bot rows that don't have one.

REPLICA IDENTITY FULL is already set on `messages` (migration 0008), so
status UPDATEs will broadcast complete row data for the Realtime
subscription that drives the tick state machine.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_messages_status_ticks"
down_revision: str | Sequence[str] | None = "0008_realtime_publish_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="sent",
        ),
    )
    op.add_column(
        "messages",
        sa.Column(
            "direction",
            sa.String(8),
            nullable=True,
        ),
    )
    op.add_column(
        "messages",
        sa.Column("client_message_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column(
            "error",
            postgresql.JSONB,
            nullable=True,
        ),
    )

    op.execute(
        """
        UPDATE public.messages
        SET direction = CASE
          WHEN role = 'user' THEN 'in'
          ELSE 'out'
        END
        WHERE direction IS NULL
        """
    )
    op.alter_column("messages", "direction", nullable=False)

    op.create_check_constraint(
        "ck_messages_status",
        "messages",
        "status IN ('pending','sent','delivered','read','failed')",
    )
    op.create_check_constraint(
        "ck_messages_direction",
        "messages",
        "direction IN ('in','out')",
    )

    op.create_index(
        "ix_messages_client_message_id",
        "messages",
        ["conversation_id", "client_message_id"],
        unique=True,
        postgresql_where=sa.text("client_message_id IS NOT NULL"),
    )
    op.create_index(
        "ix_messages_status",
        "messages",
        ["status"],
        postgresql_where=sa.text("status IN ('pending','failed')"),
    )


def downgrade() -> None:
    op.drop_index("ix_messages_status", table_name="messages")
    op.drop_index("ix_messages_client_message_id", table_name="messages")
    op.drop_constraint("ck_messages_direction", "messages", type_="check")
    op.drop_constraint("ck_messages_status", "messages", type_="check")
    op.drop_column("messages", "error")
    op.drop_column("messages", "failed_at")
    op.drop_column("messages", "read_at")
    op.drop_column("messages", "delivered_at")
    op.drop_column("messages", "client_message_id")
    op.drop_column("messages", "direction")
    op.drop_column("messages", "status")
