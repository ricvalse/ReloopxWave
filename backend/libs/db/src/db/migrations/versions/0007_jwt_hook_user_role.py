"""rewrite jwt hook to use user_role claim (don't overwrite postgres role)

Revision ID: 0007_jwt_hook_user_role
Revises: 0006_drop_super_admin
Create Date: 2026-04-28

The original hook (0002_auth_jwt_hook) wrote the application role into the
top-level `role` claim. Supabase's PostgREST + Supavisor pooler treat that
claim as the *Postgres* role to `SET ROLE` to (the canonical values are
`authenticated`, `anon`, `service_role`). When a merchant_user logged in
and any direct-Supabase query went out, Postgres tried `SET ROLE
merchant_user` and failed with `role "merchant_user" does not exist`.

Fix: write the application role to a dedicated `user_role` claim and
leave the top-level `role` untouched. Backend code still reads it (now
from `user_role`), no current RLS policy reads `role` after 0006_drop_super_admin
so nothing else needs to migrate. Existing tokens will fail role checks
until users re-login — this is acceptable since the prior behavior was
already broken for merchant_user tokens hitting Supabase directly.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_jwt_hook_user_role"
down_revision: str | Sequence[str] | None = "0006_drop_super_admin"
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
    claims := jsonb_set(claims, '{user_role}', to_jsonb(app_role));
  END IF;

  RETURN jsonb_set(event, '{claims}', claims);
END;
$fn$;
"""


def upgrade() -> None:
    op.execute(_HOOK_BODY)


def downgrade() -> None:
    op.execute(
        """
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
    )
