"""advisor fixes — pgvector schema, FK index, RLS initplan helper

Revision ID: 0004_advisor_fixes
Revises: 0003_storage_buckets
Create Date: 2026-04-22

Three things:
  1. Move `vector` out of `public` into `extensions` (Supabase convention).
  2. Index bot_configs.template_id (uncovered FK).
  3. Rewrite every RLS policy to wrap the JWT claim lookup in (SELECT ...) so
     Postgres lifts it into an init-plan and evaluates it once per query
     instead of once per row. Helper lives in public._jwt_claim(text).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_advisor_fixes"
down_revision: str | Sequence[str] | None = "0003_storage_buckets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TENANT_POLICIES = {
    "tenant_isolation_tenants": ("tenants", "id"),
    "tenant_isolation_bot_templates": ("bot_templates", "tenant_id"),
    "tenant_isolation_ft_models": ("ft_models", "tenant_id"),
    "tenant_isolation_analytics_events": ("analytics_events", "tenant_id"),
}

MERCHANT_POLICIES = (
    ("merchant_isolation_bot_configs", "bot_configs"),
    ("merchant_isolation_prompt_templates", "prompt_templates"),
    ("merchant_isolation_knowledge_base_docs", "knowledge_base_docs"),
    ("merchant_isolation_kb_chunks", "kb_chunks"),
    ("merchant_isolation_conversations", "conversations"),
    ("merchant_isolation_messages", "messages"),
    ("merchant_isolation_leads", "leads"),
    ("merchant_isolation_objections", "objections"),
    ("merchant_isolation_ab_experiments", "ab_experiments"),
    ("merchant_isolation_ab_assignments", "ab_assignments"),
    ("merchant_isolation_integrations", "integrations"),
)


def _merchant_pred(table: str) -> str:
    return f"""
        EXISTS (SELECT 1 FROM merchants m
          WHERE m.id = {table}.merchant_id
            AND m.tenant_id = (SELECT public._jwt_claim('tenant_id'))::uuid
            AND ((SELECT public._jwt_claim('merchant_id')) IS NULL
                 OR m.id = (SELECT public._jwt_claim('merchant_id'))::uuid))
    """


def upgrade() -> None:
    # On Supabase we originally installed `vector` in `public` and moved it
    # here. Fresh installs already create it in `extensions` (see 0001), so
    # this is a defensive no-op.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
            WHERE e.extname = 'vector' AND n.nspname <> 'extensions'
          ) THEN
            EXECUTE 'ALTER EXTENSION vector SET SCHEMA extensions';
          END IF;
        END $$;
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_bot_configs_template_id ON bot_configs (template_id)")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public._jwt_claim(claim_name text)
        RETURNS text
        LANGUAGE sql
        STABLE
        PARALLEL SAFE
        SET search_path = ''
        AS $fn$
          SELECT (current_setting('request.jwt.claims', true)::jsonb) ->> claim_name
        $fn$
        """
    )

    # Drop existing policies (from 0001).
    for name, (table, _) in TENANT_POLICIES.items():
        op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
    op.execute("DROP POLICY IF EXISTS tenant_or_merchant_isolation_users ON users")
    op.execute("DROP POLICY IF EXISTS merchant_isolation_merchants ON merchants")
    for name, table in MERCHANT_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")

    # Tenant-scoped: match the tenant_id claim against the row's column.
    for name, (table, column) in TENANT_POLICIES.items():
        op.execute(
            f"""
            CREATE POLICY {name} ON {table}
              USING ((SELECT public._jwt_claim('tenant_id'))::uuid = {column})
              WITH CHECK ((SELECT public._jwt_claim('tenant_id'))::uuid = {column})
            """
        )

    # users — tenant_id on the row, optional merchant_id claim.
    op.execute(
        """
        CREATE POLICY tenant_or_merchant_isolation_users ON users
          USING (
            (SELECT public._jwt_claim('tenant_id'))::uuid = tenant_id
            AND (
              (SELECT public._jwt_claim('merchant_id')) IS NULL
              OR merchant_id IS NULL
              OR (SELECT public._jwt_claim('merchant_id'))::uuid = merchant_id
            )
          )
          WITH CHECK (
            (SELECT public._jwt_claim('tenant_id'))::uuid = tenant_id
            AND (
              (SELECT public._jwt_claim('merchant_id')) IS NULL
              OR merchant_id IS NULL
              OR (SELECT public._jwt_claim('merchant_id'))::uuid = merchant_id
            )
          )
        """
    )

    # merchants — tenant_id directly, optional merchant_id claim.
    op.execute(
        """
        CREATE POLICY merchant_isolation_merchants ON merchants
          USING (
            tenant_id = (SELECT public._jwt_claim('tenant_id'))::uuid
            AND (
              (SELECT public._jwt_claim('merchant_id')) IS NULL
              OR id = (SELECT public._jwt_claim('merchant_id'))::uuid
            )
          )
          WITH CHECK (
            tenant_id = (SELECT public._jwt_claim('tenant_id'))::uuid
            AND (
              (SELECT public._jwt_claim('merchant_id')) IS NULL
              OR id = (SELECT public._jwt_claim('merchant_id'))::uuid
            )
          )
        """
    )

    # Merchant-scoped tables — join through merchants.tenant_id.
    for name, table in MERCHANT_POLICIES:
        pred = _merchant_pred(table)
        op.execute(
            f"""
            CREATE POLICY {name} ON {table}
              USING ({pred})
              WITH CHECK ({pred})
            """
        )


def downgrade() -> None:
    # Dropping the policies is fine; we don't try to restore the 0001 versions.
    for name, (table, _) in TENANT_POLICIES.items():
        op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
    op.execute("DROP POLICY IF EXISTS tenant_or_merchant_isolation_users ON users")
    op.execute("DROP POLICY IF EXISTS merchant_isolation_merchants ON merchants")
    for name, table in MERCHANT_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
    op.execute("DROP INDEX IF EXISTS ix_bot_configs_template_id")
    op.execute("DROP FUNCTION IF EXISTS public._jwt_claim(text)")
