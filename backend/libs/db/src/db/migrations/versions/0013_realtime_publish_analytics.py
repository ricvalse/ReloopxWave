"""publish analytics_events to supabase_realtime

Revision ID: 0013_realtime_publish_analytics
Revises: 0012_conversation_internal_note
Create Date: 2026-05-28

UC-11 / UC-12: the merchant and agency dashboards subscribe to `INSERT`
events on `public.analytics_events` to refresh KPI cards live. As with
`messages`/`conversations` (migration 0008), Postgres logical replication
only broadcasts row events for tables explicitly added to the
`supabase_realtime` publication. `analytics_events` was never added, so the
dashboard subscriptions received nothing and the "live" claim was false.
Add the table and set REPLICA IDENTITY FULL so the broadcast row carries
`merchant_id`/`tenant_id` (the client filters on `merchant_id`).

RLS on `analytics_events` (0001_initial + 0004_advisor_fixes) already
restricts subscribers to their own tenant/merchant, so the realtime stream
inherits the same isolation as direct reads.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013_realtime_publish_analytics"
down_revision: str | Sequence[str] | None = "0012_conversation_internal_note"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent: ALTER PUBLICATION ADD TABLE errors if the table is already
    # a member, so guard with pg_publication_tables.
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_publication_tables
            WHERE pubname = 'supabase_realtime'
              AND schemaname = 'public'
              AND tablename = 'analytics_events'
          ) THEN
            ALTER PUBLICATION supabase_realtime ADD TABLE public.analytics_events;
          END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE public.analytics_events REPLICA IDENTITY FULL")


def downgrade() -> None:
    op.execute("ALTER TABLE public.analytics_events REPLICA IDENTITY DEFAULT")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_publication_tables
            WHERE pubname = 'supabase_realtime'
              AND schemaname = 'public'
              AND tablename = 'analytics_events'
          ) THEN
            ALTER PUBLICATION supabase_realtime DROP TABLE public.analytics_events;
          END IF;
        END $$;
        """
    )
