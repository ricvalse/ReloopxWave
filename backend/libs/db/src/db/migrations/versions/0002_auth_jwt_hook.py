"""auth jwt custom-claims hook

Revision ID: 0002_auth_jwt_hook
Revises: 0001_initial
Create Date: 2026-04-22

Applied live via Supabase MCP on 2026-04-22. This Python mirror exists so a
fresh `alembic upgrade head` on an empty Postgres (CI, local Supabase CLI)
ends at the same state as production.

After the migration runs, enable the hook in the Supabase dashboard:
  Authentication → Hooks → Custom Access Token
  → point to public.custom_access_token_hook
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_auth_jwt_hook"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HOOK_BODY = """
CREATE OR REPLACE FUNCTION public.custom_access_token_hook(event jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth
AS $fn$
DECLARE
  claims        jsonb;
  uid           uuid;
  app_md        jsonb;
  app_tenant    text;
  app_merchant  text;
  app_role      text;
  row_tenant    uuid;
  row_merchant  uuid;
  row_role      text;
BEGIN
  uid := (event ->> 'user_id')::uuid;
  claims := COALESCE(event -> 'claims', '{}'::jsonb);

  SELECT raw_app_meta_data INTO app_md FROM auth.users WHERE id = uid;
  app_md := COALESCE(app_md, '{}'::jsonb);

  app_tenant   := app_md ->> 'tenant_id';
  app_merchant := app_md ->> 'merchant_id';
  app_role     := app_md ->> 'role';

  IF app_tenant IS NULL OR app_role IS NULL THEN
    SELECT tenant_id, merchant_id, role
      INTO row_tenant, row_merchant, row_role
      FROM public.users
     WHERE id = uid;
    IF row_tenant IS NOT NULL THEN
      app_tenant   := COALESCE(app_tenant, row_tenant::text);
      app_merchant := COALESCE(app_merchant, row_merchant::text);
      app_role     := COALESCE(app_role, row_role);
    END IF;
  END IF;

  IF app_tenant IS NOT NULL THEN
    claims := jsonb_set(claims, '{tenant_id}', to_jsonb(app_tenant));
  END IF;
  IF app_merchant IS NOT NULL THEN
    claims := jsonb_set(claims, '{merchant_id}', to_jsonb(app_merchant));
  END IF;
  IF app_role IS NOT NULL THEN
    claims := jsonb_set(claims, '{role}', to_jsonb(app_role));
  END IF;

  RETURN jsonb_set(event, '{claims}', claims);
END;
$fn$;
"""


def upgrade() -> None:
    op.execute(_HOOK_BODY)
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.custom_access_token_hook(jsonb) TO supabase_auth_admin"
    )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION public.custom_access_token_hook(jsonb) FROM authenticated, anon, public"
    )
    op.execute("GRANT SELECT ON TABLE public.users TO supabase_auth_admin")
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = 'public' AND tablename = 'users'
              AND policyname = 'allow_auth_admin_read_users'
          ) THEN
            CREATE POLICY allow_auth_admin_read_users ON public.users
              FOR SELECT TO supabase_auth_admin USING (true);
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS allow_auth_admin_read_users ON public.users")
    op.execute("DROP FUNCTION IF EXISTS public.custom_access_token_hook(jsonb)")
