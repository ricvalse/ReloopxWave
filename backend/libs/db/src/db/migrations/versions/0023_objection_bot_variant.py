"""objection bot variant (UC-13)

Revision ID: 0023_objection_bot_variant
Revises: 0022_kb_doc_status_detail
Create Date: 2026-06-18

Adds `objections.bot_variant` so the objection report can be filtered by A/B
variant (UC-13). Captured from the conversation's `variant_id` at extraction.
Nullable; indexed per-merchant for the filter. No RLS change: `objections`
already carries `merchant_isolation_objections` from 0001.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_objection_bot_variant"
down_revision: str | Sequence[str] | None = "0022_kb_doc_status_detail"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("objections", sa.Column("bot_variant", sa.String(length=32), nullable=True))
    op.create_index("ix_objections_merchant_variant", "objections", ["merchant_id", "bot_variant"])


def downgrade() -> None:
    op.drop_index("ix_objections_merchant_variant", table_name="objections")
    op.drop_column("objections", "bot_variant")
