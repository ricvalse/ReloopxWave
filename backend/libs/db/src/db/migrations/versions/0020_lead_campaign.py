"""lead campaign attribution

Revision ID: 0020_lead_campaign
Revises: 0019_analytics_merchant_rls
Create Date: 2026-06-17

Adds `leads.campaign` (UC-11 "filtri per campagna"). Captured at first contact
from the click-to-WhatsApp ad `referral` on the inbound message; NULL for
organic leads. Indexed per-merchant for the dashboard campaign filter. No RLS
change: `leads` already carries `merchant_isolation_leads` from 0001.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_lead_campaign"
down_revision: str | Sequence[str] | None = "0019_analytics_merchant_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("campaign", sa.String(length=200), nullable=True))
    op.create_index("ix_leads_merchant_campaign", "leads", ["merchant_id", "campaign"])


def downgrade() -> None:
    op.drop_index("ix_leads_merchant_campaign", table_name="leads")
    op.drop_column("leads", "campaign")
