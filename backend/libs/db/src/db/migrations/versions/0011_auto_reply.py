"""conversations.auto_reply for per-thread bot takeover

Revision ID: 0011_auto_reply
Revises: 0010_lock_search_paths
Create Date: 2026-04-29

Adds a per-conversation switch the merchant can flip from the chat UI
("Risposta automatica" toggle in the thread header) to silence the bot
on a single thread when an agent takes over via the composer. Pairs
with the merchant-wide `bot.auto_reply_enabled` override that lives in
`bot_configs.overrides`; the worker treats them as AND — if either is
false, the LLM turn is skipped (the inbound is still persisted and
analytics still fire, just no assistant message is generated or sent).

Default true so the existing fleet keeps replying; merchants opt out
explicitly per-thread or per-account.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_auto_reply"
down_revision: str | Sequence[str] | None = "0010_lock_search_paths"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "auto_reply",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "auto_reply")
