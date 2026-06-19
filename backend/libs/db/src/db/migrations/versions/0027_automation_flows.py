"""automation_flows / automation_nodes / automation_edges — visual flow builder

Revision ID: 0027_automation_flows
Revises: 0026_whatsapp_template_examples
Create Date: 2026-06-19

The "lavagnetta" automation builder (scope A, graph model). Distinct from the
legacy linear `flows`/`flow_steps`:

  * `automation_flows` — one row per custom automation a merchant draws. Carries
    a denormalised `trigger_type` so the worker dispatcher can find subscribers
    for an event with a single indexed lookup.
  * `automation_nodes` — trigger | condition | action nodes, with a client
    `node_key` referenced by edges and a canvas x/y position.
  * `automation_edges` — directed wires between nodes (`branch` = default|true|
    false for the two sides of a condition).

All three are merchant-scoped and carry the same RLS as 0014
(`merchant_isolation_<table>`): the tenant boundary is enforced via an EXISTS
join through `merchants.tenant_id`; an agency user (no merchant claim) sees every
row under their tenant, a merchant user is pinned to their own. `merchant_id` is
denormalised onto nodes/edges so the predicate applies without a second join.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_automation_flows"
down_revision: str | Sequence[str] | None = "0026_whatsapp_template_examples"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_MERCHANT_SCOPED = ("automation_flows", "automation_nodes", "automation_edges")


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
    # ---- automation_flows ----
    op.create_table(
        "automation_flows",
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
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("trigger_type", sa.String(64)),
        sa.Column(
            "trigger_config", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("canvas", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_ts_columns(),
    )
    op.create_index("ix_automation_flows_merchant_id", "automation_flows", ["merchant_id"])
    op.create_index(
        "ix_automation_flows_merchant_trigger",
        "automation_flows",
        ["merchant_id", "trigger_type"],
    )

    # ---- automation_nodes ----
    op.create_table(
        "automation_nodes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "automation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation_flows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("position_x", sa.Float, nullable=False, server_default="0"),
        sa.Column("position_y", sa.Float, nullable=False, server_default="0"),
        *_ts_columns(),
        sa.UniqueConstraint("automation_id", "node_key", name="uq_automation_nodes_key"),
    )
    op.create_index("ix_automation_nodes_automation_id", "automation_nodes", ["automation_id"])
    op.create_index("ix_automation_nodes_merchant_id", "automation_nodes", ["merchant_id"])

    # ---- automation_edges ----
    op.create_table(
        "automation_edges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "automation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation_flows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("target_key", sa.String(64), nullable=False),
        sa.Column("branch", sa.String(16), nullable=False, server_default="default"),
        *_ts_columns(),
    )
    op.create_index("ix_automation_edges_automation_id", "automation_edges", ["automation_id"])
    op.create_index("ix_automation_edges_merchant_id", "automation_edges", ["merchant_id"])

    # ---- Row-Level Security (mirror 0014 merchant-scoped pattern) ----
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
