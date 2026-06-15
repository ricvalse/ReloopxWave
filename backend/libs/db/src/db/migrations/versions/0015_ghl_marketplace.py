"""ghl marketplace: agency installs + per-location tokens

Revision ID: 0015_ghl_marketplace
Revises: 0014_whatsapp_templates_flows
Create Date: 2026-06-15

Turns the GHL integration from single-location/merchant-initiated into the
marketplace agency-install model (ADR 0007). Two new tables, two cardinalities:

  * `ghl_agency_installs` — one Company (Agency-level) token per tenant, plus the
    `company_id -> tenant` mapping the INSTALL webhook resolves against.
    Tenant-scoped RLS (direct `tenant_id` match, like `tenant_isolation_*`).
  * `ghl_location_tokens` — one Location-level token per installed `locationId`.
    `merchant_id` is nullable (a location can be installed before an agency admin
    links it to a merchant); `tenant_id` is denormalised so RLS needs no join and
    the webhook can look a row up by `location_id` in O(1). RLS mirrors the
    `users` table's tenant-OR-merchant predicate.

Writes from the OAuth callback / INSTALL worker go through an unscoped
service-role `session_scope()` (no JWT claims), exactly as the existing GHL
OAuth callback already writes to `integrations`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_ghl_marketplace"
down_revision: str | Sequence[str] | None = "0014_whatsapp_templates_flows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_TABLES = ("ghl_agency_installs", "ghl_location_tokens")


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
    # ---- ghl_agency_installs (tenant-scoped) ----
    op.create_table(
        "ghl_agency_installs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("company_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("secret_ciphertext", sa.LargeBinary, nullable=False),
        sa.Column("secret_nonce", sa.LargeBinary, nullable=False),
        sa.Column("secret_aad", sa.LargeBinary),
        sa.Column("kek_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("company_name", sa.String(200)),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_ts_columns(),
        sa.UniqueConstraint("company_id", name="uq_ghl_agency_installs_company_id"),
        sa.UniqueConstraint("tenant_id", name="uq_ghl_agency_installs_tenant_id"),
    )
    op.create_index("ix_ghl_agency_installs_tenant_id", "ghl_agency_installs", ["tenant_id"])
    op.create_index("ix_ghl_agency_installs_company_id", "ghl_agency_installs", ["company_id"])

    # ---- ghl_location_tokens (tenant-scoped, merchant_id nullable) ----
    op.create_table(
        "ghl_location_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("company_id", sa.String(64), nullable=False),
        sa.Column("location_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending_link"),
        sa.Column("secret_ciphertext", sa.LargeBinary),
        sa.Column("secret_nonce", sa.LargeBinary),
        sa.Column("secret_aad", sa.LargeBinary),
        sa.Column("kek_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("location_name", sa.String(200)),
        sa.Column("installed_by_user_id", sa.String(64)),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_ts_columns(),
        sa.UniqueConstraint("location_id", name="uq_ghl_location_tokens_location_id"),
    )
    op.create_index("ix_ghl_location_tokens_tenant_id", "ghl_location_tokens", ["tenant_id"])
    op.create_index("ix_ghl_location_tokens_merchant_id", "ghl_location_tokens", ["merchant_id"])
    op.create_index("ix_ghl_location_tokens_company_id", "ghl_location_tokens", ["company_id"])
    op.create_index("ix_ghl_location_tokens_location_id", "ghl_location_tokens", ["location_id"])

    # ---- Row-Level Security ----
    for table in _NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # Agency installs: tenant-scoped (direct tenant_id match, mirrors tenant_isolation_*).
    op.execute(
        """
        CREATE POLICY tenant_isolation_ghl_agency_installs ON ghl_agency_installs
        USING ((current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid = tenant_id)
        WITH CHECK ((current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid = tenant_id)
        """
    )

    # Location tokens: tenant must match; merchant claim (if present) must match or
    # the row must be unlinked. Mirrors tenant_or_merchant_isolation_users — an
    # agency user (no merchant claim) sees all tenant locations incl. pending_link,
    # a merchant user sees only their own.
    predicate = """
        (current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid = tenant_id
        AND (
            (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id') IS NULL
            OR merchant_id IS NULL
            OR (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')::uuid = merchant_id
        )
    """
    op.execute(
        f"""
        CREATE POLICY tenant_or_merchant_isolation_ghl_location_tokens ON ghl_location_tokens
        USING ({predicate})
        WITH CHECK ({predicate})
        """
    )


def downgrade() -> None:
    for table in reversed(_NEW_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
