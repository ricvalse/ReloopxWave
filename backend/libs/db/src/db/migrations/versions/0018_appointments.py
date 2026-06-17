"""appointments — local mirror of GHL calendar appointments (UC-02)

Revision ID: 0018_appointments
Revises: 0017_ab_experiment_winner
Create Date: 2026-06-17

GHL stays the system of record for appointments (it owns the calendar). This
table is a derived read-model kept in sync by write-through (the bot's
book/reschedule/cancel) plus a reconcile poll — GHL sends no appointment
webhooks, so the poll is the only way manual GHL-side changes reach the mirror.

`ghl_appointment_id` is the join handle back to the GHL event; the live booking
flow used to drop it, which made reschedule/cancel/reconcile impossible. It is
unique per merchant so the Tier-2 reconcile poll can upsert idempotently.

Merchant-scoped with RLS mirroring 0001_initial / 0016 (`merchant_isolation_<table>`):
the tenant boundary is an EXISTS join through `merchants.tenant_id`; an agency
user (no merchant claim) sees every row under their tenant, a merchant user is
pinned to their own.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_appointments"
down_revision: str | Sequence[str] | None = "0017_ab_experiment_winner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_MERCHANT_SCOPED = ("appointments",)


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
    op.create_table(
        "appointments",
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
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ghl_appointment_id", sa.String(120)),
        sa.Column("ghl_contact_id", sa.String(120)),
        sa.Column("calendar_id", sa.String(120)),
        sa.Column("title", sa.String(300)),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True)),
        sa.Column("tz_name", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False, server_default="booked"),
        sa.Column("source", sa.String(16), nullable=False, server_default="bot"),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_ts_columns(),
        sa.UniqueConstraint(
            "merchant_id", "ghl_appointment_id", name="uq_appointments_merchant_ghl_event"
        ),
    )
    op.create_index("ix_appointments_merchant_id", "appointments", ["merchant_id"])
    op.create_index("ix_appointments_lead_id", "appointments", ["lead_id"])
    op.create_index("ix_appointments_merchant_start", "appointments", ["merchant_id", "start_at"])

    # ---- Row-Level Security (mirror 0001_initial / 0016 merchant-scoped) ----
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
