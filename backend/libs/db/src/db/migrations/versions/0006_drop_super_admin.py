"""drop super_admin role — collapse to agency_admin + merchant_user

Revision ID: 0006_drop_super_admin
Revises: 0005_super_admin_support
Create Date: 2026-04-24

The product is a single-agency platform (Wave Marketing) with per-merchant
portals underneath. The `super_admin` tier was only there to solve the
chicken-and-egg of creating the first tenant without already being a member
of one — now handled by `POST /auth/bootstrap` instead.

This migration:
  1. Drops every `super_admin_bypass_*` policy added in 0005.
  2. Removes the seeded `platform` tenant row (safe because no merchants
     were ever attached to it).

If no `wave` tenant has been bootstrapped yet the deployment will be empty
afterwards — exactly the state `POST /auth/bootstrap` expects when the
first admin signs in.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_drop_super_admin"
down_revision: str | Sequence[str] | None = "0005_super_admin_support"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORM_TENANT_ID = "00000000-0000-0000-0000-000000000001"

RLS_TABLES = (
    "tenants",
    "bot_templates",
    "ft_models",
    "analytics_events",
    "merchants",
    "users",
    "bot_configs",
    "prompt_templates",
    "knowledge_base_docs",
    "kb_chunks",
    "conversations",
    "messages",
    "leads",
    "objections",
    "ab_experiments",
    "ab_assignments",
    "integrations",
)


def upgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS super_admin_bypass_{table} ON {table}")
    op.execute(f"DELETE FROM tenants WHERE id = '{PLATFORM_TENANT_ID}'")


def downgrade() -> None:
    op.execute(
        f"""
        INSERT INTO tenants (id, slug, name, status, settings)
        VALUES (
            '{PLATFORM_TENANT_ID}',
            'platform',
            'Platform',
            'active',
            '{{"is_platform": true}}'::jsonb
        )
        ON CONFLICT (id) DO NOTHING
        """
    )
    for table in RLS_TABLES:
        op.execute(
            f"""
            CREATE POLICY super_admin_bypass_{table} ON {table}
              FOR ALL
              USING ((SELECT public._jwt_claim('role')) = 'super_admin')
              WITH CHECK ((SELECT public._jwt_claim('role')) = 'super_admin')
            """
        )
