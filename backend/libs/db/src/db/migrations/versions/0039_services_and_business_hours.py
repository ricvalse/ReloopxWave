"""Servizi prenotabili e orari di apertura (UC-02 booking)

Revision ID: 0039_services_biz_hours
Revises: 0038_appt_reminder_schedule
Create Date: 2026-06-27

Aggiunge tre tabelle per il calendario di prenotazione:

  services         — catalogo servizi con durata e prezzo opzionale
  business_hours   — orari di apertura per giorno della settimana
  business_closures — chiusure eccezionali (festività, ferie)

Aggiunge anche la colonna nullable `service_id` in `appointments` così ogni
appuntamento tiene traccia di quale servizio è stato prenotato.

Tutte e tre le nuove tabelle hanno RLS merchant-scoped (stesso predicato delle
altre tabelle merchant-scoped nel progetto).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0039_services_biz_hours"
down_revision = "0038_appt_reminder_schedule"
branch_labels = None
depends_on = None

# Predicato RLS riusabile — stessa logica di kb_gaps, appointments, ecc.
def _rls_predicate(table: str) -> str:
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


def _enable_rls(table: str) -> None:
    pred = _rls_predicate(table)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY merchant_isolation_{table} ON {table}
        USING ({pred})
        WITH CHECK ({pred})
        """
    )


def _disable_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS merchant_isolation_{table} ON {table}")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # ── services ─────────────────────────────────────────────────────────────
    op.create_table(
        "services",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("merchant_id", UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("handle", sa.String(120), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("duration_min", sa.Integer, nullable=False),
        sa.Column("buffer_min", sa.Integer, nullable=False, server_default="0"),
        sa.Column("price", sa.Numeric(10, 2)),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("ghl_calendar_id", sa.String(120)),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("meta", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("merchant_id", "handle", name="uq_services_merchant_handle"),
        sa.CheckConstraint("duration_min >= 5 AND duration_min <= 480", name="ck_services_duration"),
        sa.CheckConstraint("buffer_min >= 0 AND buffer_min <= 120", name="ck_services_buffer"),
        sa.CheckConstraint("price IS NULL OR price >= 0", name="ck_services_price"),
    )
    op.create_index("ix_services_merchant_active_sort", "services", ["merchant_id", "is_active", "sort_order"])
    _enable_rls("services")

    # ── business_hours ────────────────────────────────────────────────────────
    op.create_table(
        "business_hours",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("merchant_id", UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("day_of_week", sa.SmallInteger, nullable=False),
        sa.Column("is_open", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("open_time", sa.Time),
        sa.Column("close_time", sa.Time),
        sa.Column("break_start", sa.Time),
        sa.Column("break_end", sa.Time),
        sa.UniqueConstraint("merchant_id", "day_of_week", name="uq_business_hours_merchant_day"),
        sa.CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="ck_business_hours_dow"),
        sa.CheckConstraint(
            "NOT is_open OR (open_time IS NOT NULL AND close_time IS NOT NULL AND open_time < close_time)",
            name="ck_business_hours_times",
        ),
    )
    _enable_rls("business_hours")

    # ── business_closures ─────────────────────────────────────────────────────
    op.create_table(
        "business_closures",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("merchant_id", UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("closed_on", sa.Date, nullable=False),
        sa.Column("label", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("merchant_id", "closed_on", name="uq_business_closures_merchant_date"),
    )
    op.create_index("ix_business_closures_merchant_date", "business_closures", ["merchant_id", "closed_on"])
    _enable_rls("business_closures")

    # ── appointments.service_id ───────────────────────────────────────────────
    op.add_column(
        "appointments",
        sa.Column(
            "service_id",
            UUID(as_uuid=True),
            sa.ForeignKey("services.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_appointments_service", "appointments", ["service_id"])


def downgrade() -> None:
    op.drop_index("ix_appointments_service", table_name="appointments")
    op.drop_column("appointments", "service_id")

    _disable_rls("business_closures")
    op.drop_index("ix_business_closures_merchant_date", table_name="business_closures")
    op.drop_table("business_closures")

    _disable_rls("business_hours")
    op.drop_table("business_hours")

    _disable_rls("services")
    op.drop_index("ix_services_merchant_active_sort", table_name="services")
    op.drop_table("services")
