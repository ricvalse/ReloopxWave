"""lead opt-out (UC-06)

Revision ID: 0021_leads_opted_out
Revises: 0020_lead_campaign
Create Date: 2026-06-18

Adds `leads.opted_out_at` — set when a lead replies STOP/CANCELLA. Opted-out
leads are excluded from reactivation (UC-06) and the bot stops auto-replying.
Nullable; indexed per-merchant so the reactivation scan filters cheaply. No RLS
change: `leads` already carries `merchant_isolation_leads` from 0001.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_leads_opted_out"
down_revision: str | Sequence[str] | None = "0020_lead_campaign"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_leads_merchant_opted_out", "leads", ["merchant_id", "opted_out_at"])


def downgrade() -> None:
    op.drop_index("ix_leads_merchant_opted_out", table_name="leads")
    op.drop_column("leads", "opted_out_at")
