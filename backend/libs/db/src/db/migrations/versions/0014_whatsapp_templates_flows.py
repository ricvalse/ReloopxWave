"""whatsapp templates + flows + flow_steps; conversations.last_inbound_at

Revision ID: 0014_whatsapp_templates_flows
Revises: 0013_realtime_publish_analytics
Create Date: 2026-06-14

Adds the WhatsApp template engine and the lightweight "Flussi" layer:

  * `whatsapp_templates` — per-merchant 360dialog message templates (create →
    submit → approval lifecycle). Distinct from `bot_templates`/`prompt_templates`
    (UC-10 bot prompt config) — these are Meta-approved WhatsApp message templates
    required to message a user outside the 24h customer-service window.
  * `flows` / `flow_steps` — configurable outbound sequences (scope A). Each step
    binds a lifecycle trigger to a template + variable mapping + window policy.
    The schedulers (no-answer, reactivation, booking reminder, first contact)
    become executors that read steps instead of hardcoded copy.
  * `conversations.last_inbound_at` — timestamp of the last *customer* message,
    used to decide free-text (inside 24h) vs approved template (outside).
    `last_message_at` can't serve this: it is bumped by outbound sends too.

All new tables are merchant-scoped and carry RLS mirroring 0001_initial
(`merchant_isolation_<table>`): the tenant boundary is enforced via an EXISTS
join through `merchants.tenant_id`, and an agency user (no merchant claim) sees
every row under their tenant while a merchant user is pinned to their own. We
denormalise `merchant_id` onto `flow_steps` so the same predicate applies
without a second join.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_whatsapp_templates_flows"
down_revision: str | Sequence[str] | None = "0013_realtime_publish_analytics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_MERCHANT_SCOPED = ("whatsapp_templates", "flows", "flow_steps")


def _ts_columns() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
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


def upgrade() -> None:
    # ---- conversations.last_inbound_at ----
    op.add_column(
        "conversations",
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill from the existing message history so conversations that are
    # in-flight at deploy time aren't all treated as "outside the 24h window"
    # (which would silently drop their next legitimate free-text reminder).
    op.execute(
        """
        UPDATE conversations c
        SET last_inbound_at = sub.max_in
        FROM (
            SELECT conversation_id, MAX(created_at) AS max_in
            FROM messages
            WHERE direction = 'in'
            GROUP BY conversation_id
        ) sub
        WHERE c.id = sub.conversation_id
        """
    )

    # ---- whatsapp_templates ----
    op.create_table(
        "whatsapp_templates",
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
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("category", sa.String(32), nullable=False, server_default="UTILITY"),
        sa.Column("language", sa.String(16), nullable=False, server_default="it"),
        sa.Column("purpose", sa.String(64), nullable=False, server_default="custom"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("header_type", sa.String(16), nullable=False, server_default="NONE"),
        sa.Column("header_text", sa.Text),
        sa.Column("header_image_url", sa.String(1024)),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("footer", sa.String(120)),
        sa.Column("buttons", postgresql.JSONB),
        sa.Column(
            "variables", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "variable_sources",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("whatsapp_template_id", sa.String(128)),
        sa.Column("meta_status", sa.String(32)),
        sa.Column("meta_quality", sa.String(16)),
        sa.Column("rejection_reason", sa.Text),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column("meta_last_synced_at", sa.DateTime(timezone=True)),
        *_ts_columns(),
        sa.UniqueConstraint("merchant_id", "name", name="uq_whatsapp_templates_merchant_id"),
    )
    op.create_index("ix_whatsapp_templates_merchant_id", "whatsapp_templates", ["merchant_id"])
    op.create_index(
        "ix_whatsapp_templates_merchant_purpose",
        "whatsapp_templates",
        ["merchant_id", "purpose"],
    )
    op.create_index(
        "ix_whatsapp_templates_merchant_status",
        "whatsapp_templates",
        ["merchant_id", "status"],
    )

    # ---- flows ----
    op.create_table(
        "flows",
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
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_ts_columns(),
        sa.UniqueConstraint("merchant_id", "key", name="uq_flows_merchant_id"),
    )
    op.create_index("ix_flows_merchant_id", "flows", ["merchant_id"])

    # ---- flow_steps ----
    op.create_table(
        "flow_steps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "flow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("flows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Denormalised so the standard merchant RLS predicate applies directly.
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer, nullable=False),
        sa.Column("delay_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("whatsapp_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "variable_mapping",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("window_policy", sa.String(24), nullable=False, server_default="auto"),
        sa.Column("free_text", sa.Text),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        *_ts_columns(),
        sa.UniqueConstraint("flow_id", "step_index", name="uq_flow_steps_flow_id"),
    )
    op.create_index("ix_flow_steps_flow_id", "flow_steps", ["flow_id"])
    op.create_index("ix_flow_steps_merchant_id", "flow_steps", ["merchant_id"])

    # ---- Row-Level Security (mirror 0001_initial merchant-scoped pattern) ----
    def _merchant_scoped_predicate(table: str) -> str:
        return f"""
            EXISTS (
                SELECT 1 FROM merchants m
                WHERE m.id = {table}.merchant_id
                  AND m.tenant_id = (current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid
                  AND (
                      (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id') IS NULL
                      OR m.id = (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')::uuid
                  )
            )
        """

    for table in _NEW_MERCHANT_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        predicate = _merchant_scoped_predicate(table)
        op.execute(
            f"""
            CREATE POLICY merchant_isolation_{table} ON {table}
            USING ({predicate})
            WITH CHECK ({predicate})
            """
        )


def downgrade() -> None:
    for table in reversed(_NEW_MERCHANT_SCOPED):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.drop_column("conversations", "last_inbound_at")
