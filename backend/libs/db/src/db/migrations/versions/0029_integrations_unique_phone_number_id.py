"""add unique index on integrations(provider, phone_number_id) for active rows

Revision ID: 0029_integrations_unique_phone_number_id
Revises: 0028_merge_lifecycle_flows
Create Date: 2026-06-26

A WhatsApp phone_number_id identifies a single Business Account channel — it
cannot belong to two merchants simultaneously. This partial unique index
(filtered on status='active' and provider='whatsapp') prevents the phantom-row
scenario where a number transferred from one merchant to another still has the
old merchant's row sitting as active in the integrations table.

The application-level fix in upsert_whatsapp() removes the stale row at write
time; this index is the belt-and-suspenders guard at the DB level.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_integrations_unique_phone_number_id"
down_revision = "0028_merge_lifecycle_flows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # First, clean up any existing duplicates by keeping only the most-recently
    # updated row per (provider, phone_number_id) pair.
    op.execute(
        """
        DELETE FROM integrations i1
        USING integrations i2
        WHERE i1.provider = 'whatsapp'
          AND i2.provider = 'whatsapp'
          AND i1.meta->>'phone_number_id' IS NOT NULL
          AND i1.meta->>'phone_number_id' = i2.meta->>'phone_number_id'
          AND i1.status = 'active'
          AND i2.status = 'active'
          AND i1.merchant_id != i2.merchant_id
          AND i1.updated_at < i2.updated_at
        """
    )

    # Partial unique index: only one active whatsapp row per phone_number_id.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_integrations_active_wa_phone_number_id
        ON integrations ((meta->>'phone_number_id'))
        WHERE provider = 'whatsapp' AND status = 'active'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_integrations_active_wa_phone_number_id")
