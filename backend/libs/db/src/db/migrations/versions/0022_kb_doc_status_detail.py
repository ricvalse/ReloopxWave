"""kb doc status detail + last error (UC-07)

Revision ID: 0022_kb_doc_status_detail
Revises: 0021_leads_opted_out
Create Date: 2026-06-18

Adds `knowledge_base_docs.status_detail` (human-readable status note) and
`last_error` (failure reason) so the KB UI can show WHY a doc is stuck/failed,
not just the bare `status`. Both nullable. No RLS change.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_kb_doc_status_detail"
down_revision: str | Sequence[str] | None = "0021_leads_opted_out"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_base_docs", sa.Column("status_detail", sa.String(length=300), nullable=True)
    )
    op.add_column(
        "knowledge_base_docs", sa.Column("last_error", sa.String(length=1000), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("knowledge_base_docs", "last_error")
    op.drop_column("knowledge_base_docs", "status_detail")
