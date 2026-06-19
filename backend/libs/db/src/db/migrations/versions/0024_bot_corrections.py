"""bot corrections (playground response-fix loop)

Revision ID: 0024_bot_corrections
Revises: 0023_objection_bot_variant
Create Date: 2026-06-19

Adds `bot_corrections` — the "Amalia-style" playground feedback loop. When a
merchant edits a bad bot reply in the playground we store the triggering
customer message, the original response and the corrected one. Active rows are
matched against the live/playground customer message and injected into the
system prompt as mandatory overrides, scoped to the owning merchant only.

Merchant-scoped + RLS mirroring 0016_catalog_policies_faq
(`merchant_isolation_<table>`): the tenant boundary is enforced via an EXISTS
join through `merchants.tenant_id`; an agency user (no merchant claim) sees
every row under their tenant, a merchant user is pinned to their own.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_bot_corrections"
down_revision: str | Sequence[str] | None = "0023_objection_bot_variant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_corrections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger_message", sa.Text, nullable=False),
        sa.Column("original_response", sa.Text, nullable=False),
        sa.Column("corrected_response", sa.Text, nullable=False),
        sa.Column("context", sa.Text),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_bot_corrections_merchant_id", "bot_corrections", ["merchant_id"])
    op.create_index(
        "ix_bot_corrections_merchant_active",
        "bot_corrections",
        ["merchant_id", "is_active"],
    )

    # ---- Row-Level Security (mirror 0016_catalog_policies_faq) ----
    predicate = """
        EXISTS (
            SELECT 1 FROM merchants m
            WHERE m.id = bot_corrections.merchant_id
              AND m.tenant_id = (current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid
              AND (
                  (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id') IS NULL
                  OR m.id = (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')::uuid
              )
        )
    """
    op.execute("ALTER TABLE bot_corrections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE bot_corrections FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY merchant_isolation_bot_corrections ON bot_corrections
        USING ({predicate})
        WITH CHECK ({predicate})
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bot_corrections CASCADE")
