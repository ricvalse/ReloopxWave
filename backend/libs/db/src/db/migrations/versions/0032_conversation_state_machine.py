"""conversations.current_state — FSM state per conversation turn

Revision ID: 0032_conv_state_machine
Revises: 0031_appt_reminder_due_at
Create Date: 2026-06-26

Aggiunge `current_state` VARCHAR(32) a `conversations`. Valori validi:
GREETING | QUALIFYING | PITCHING | OBJECTION_HANDLING | CLOSING | BOOKED | DEAD | ESCALATED.
NULL = riga legacy, trattata come GREETING dal ConversationService.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_conv_state_machine"
down_revision = "0031_appt_reminder_due_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("current_state", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "current_state")
