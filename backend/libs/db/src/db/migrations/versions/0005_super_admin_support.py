"""super_admin support — platform tenant seed + bypass policies

Revision ID: 0005_super_admin_support
Revises: 0004_advisor_fixes
Create Date: 2026-04-23

Reloop staff users carry `role = "super_admin"` in their JWT claims and need
a valid `tenant_id` claim so `get_tenant_context` keeps working. We seed a
well-known "platform" tenant row they can point at without colliding with
any real agency.

The policy additions below OR alongside each table's existing
`tenant_isolation_*` / `merchant_isolation_*` policy, so a super_admin
call sees every row regardless of the row's tenant_id. Regular users are
unaffected because their policy stays in place and the super_admin one
only matches when `claim.role = 'super_admin'`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_super_admin_support"
down_revision: str | Sequence[str] | None = "0004_advisor_fixes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORM_TENANT_ID = "00000000-0000-0000-0000-000000000001"

RLS_TABLES = (
    # tenant-scoped
    "tenants",
    "bot_templates",
    "ft_models",
    "analytics_events",
    # merchant-scoped
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
    # All interpolated values below are compile-time constants (PLATFORM_TENANT_ID
    # + table names from RLS_TABLES) — never user input, so S608 is noise.
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
        """  # noqa: S608
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


def downgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS super_admin_bypass_{table} ON {table}")
    op.execute(f"DELETE FROM tenants WHERE id = '{PLATFORM_TENANT_ID}'")  # noqa: S608
