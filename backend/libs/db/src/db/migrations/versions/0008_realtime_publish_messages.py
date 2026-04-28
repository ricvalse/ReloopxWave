"""publish messages + conversations to supabase_realtime

Revision ID: 0008_realtime_publish_messages
Revises: 0007_jwt_hook_user_role
Create Date: 2026-04-28

The merchant portal's Conversazioni view subscribes to `INSERT` events on
`public.messages` to refresh the list and open thread without a manual
reload. Postgres logical replication only broadcasts row events for
tables explicitly added to the `supabase_realtime` publication — by
default it's empty, so the subscription was getting nothing. Add the two
tables and set REPLICA IDENTITY FULL so future UPDATEs (e.g. status
changes) carry complete row data when we add those subscriptions.

RLS on both tables already restricts merchant_user subscribers to their
own merchant via the policies created in 0001_initial.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_realtime_publish_messages"
down_revision: str | Sequence[str] | None = "0007_jwt_hook_user_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent: ALTER PUBLICATION ADD TABLE errors if the table is
    # already a member, so guard with pg_publication_tables.
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_publication_tables
            WHERE pubname = 'supabase_realtime'
              AND schemaname = 'public'
              AND tablename = 'messages'
          ) THEN
            ALTER PUBLICATION supabase_realtime ADD TABLE public.messages;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_publication_tables
            WHERE pubname = 'supabase_realtime'
              AND schemaname = 'public'
              AND tablename = 'conversations'
          ) THEN
            ALTER PUBLICATION supabase_realtime ADD TABLE public.conversations;
          END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE public.messages REPLICA IDENTITY FULL")
    op.execute("ALTER TABLE public.conversations REPLICA IDENTITY FULL")


def downgrade() -> None:
    op.execute("ALTER TABLE public.conversations REPLICA IDENTITY DEFAULT")
    op.execute("ALTER TABLE public.messages REPLICA IDENTITY DEFAULT")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_publication_tables
            WHERE pubname = 'supabase_realtime'
              AND schemaname = 'public'
              AND tablename = 'conversations'
          ) THEN
            ALTER PUBLICATION supabase_realtime DROP TABLE public.conversations;
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_publication_tables
            WHERE pubname = 'supabase_realtime'
              AND schemaname = 'public'
              AND tablename = 'messages'
          ) THEN
            ALTER PUBLICATION supabase_realtime DROP TABLE public.messages;
          END IF;
        END $$;
        """
    )
