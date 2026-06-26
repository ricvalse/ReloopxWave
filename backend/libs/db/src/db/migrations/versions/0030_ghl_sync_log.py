"""ghl_sync_log — log di ogni chiamata API verso GoHighLevel

Revision ID: 0030_ghl_sync_log
Revises: 0029_uq_wa_phone_number_id
Create Date: 2026-06-26

Una riga per operazione GHL (contact.upserted, booking.created, ecc.).
Dà agli operatori visibilità completa su cosa la piattaforma ha inviato a GHL,
inclusi errori e gli ID entità GHL per cross-referencing.

RLS: stessa logica di analytics_events — ogni tenant vede solo i propri log.
Il merchant vede solo i log del proprio merchant_id.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0030_ghl_sync_log"
down_revision = "0029_uq_wa_phone_number_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ghl_sync_log",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("merchant_id", sa.UUID(), nullable=True),
        sa.Column("lead_id", sa.UUID(), nullable=True),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("ghl_entity_type", sa.String(32), nullable=True),
        sa.Column("ghl_entity_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="success"),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_ghl_sync_log_tenant_id", "ghl_sync_log", ["tenant_id"])
    op.create_index("ix_ghl_sync_log_merchant_id", "ghl_sync_log", ["merchant_id"])
    op.create_index("ix_ghl_sync_log_lead_id", "ghl_sync_log", ["lead_id"])
    op.create_index("ix_ghl_sync_log_operation", "ghl_sync_log", ["operation"])
    op.create_index("ix_ghl_sync_log_occurred_at", "ghl_sync_log", ["occurred_at"])

    # RLS: abilita e aggiungi policy per tenant isolation
    op.execute("ALTER TABLE ghl_sync_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ghl_sync_log FORCE ROW LEVEL SECURITY")

    # Agency admin vede tutti i log del proprio tenant
    op.execute(
        """
        CREATE POLICY ghl_sync_log_tenant_isolation ON ghl_sync_log
        FOR ALL
        USING (tenant_id::text = (auth.jwt() ->> 'tenant_id'))
        """
    )

    # Merchant vede solo i propri log (o quelli senza merchant_id)
    op.execute(
        """
        CREATE POLICY ghl_sync_log_merchant_isolation ON ghl_sync_log
        FOR ALL
        USING (
            merchant_id IS NULL
            OR merchant_id::text = (auth.jwt() ->> 'merchant_id')
            OR (auth.jwt() ->> 'role') IN ('agency_admin', 'worker')
        )
        """
    )

    # Il worker può inserire senza restrizioni merchant
    op.execute(
        """
        CREATE POLICY ghl_sync_log_worker_insert ON ghl_sync_log
        FOR INSERT
        WITH CHECK (tenant_id::text = (auth.jwt() ->> 'tenant_id'))
        """
    )

    # Pubblica su Realtime per live updates nella UI (opzionale, best-effort)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
                ALTER PUBLICATION supabase_realtime ADD TABLE ghl_sync_log;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
                ALTER PUBLICATION supabase_realtime DROP TABLE ghl_sync_log;
            END IF;
        END
        $$
        """
    )
    op.drop_table("ghl_sync_log")
