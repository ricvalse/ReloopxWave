"""whatsapp_templates.body_examples — persist per-variable sample values

Revision ID: 0026_whatsapp_template_examples
Revises: 0025_conversation_handoff
Create Date: 2026-06-19

Stores the merchant's chosen sample value per `{{n}}` on the template row so a
*draft* keeps its examples through to submit time (Meta requires an example for
every body variable, and good examples reduce rejection risk). Previously the
examples were passed transiently on the create call and lost if the merchant
saved a draft and submitted later.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_whatsapp_template_examples"
down_revision: str | Sequence[str] | None = "0025_conversation_handoff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_templates",
        sa.Column(
            "body_examples",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_templates", "body_examples")
