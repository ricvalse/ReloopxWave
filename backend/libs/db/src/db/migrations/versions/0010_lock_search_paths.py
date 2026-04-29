"""lock function search_paths + relocate alembic_version

Revision ID: 0010_lock_search_paths
Revises: 0009_messages_status_ticks
Create Date: 2026-04-29

Port-back of the Supabase MCP migration `0005_lock_search_paths` that was
applied directly to the live DB on 2026-04-22 (Supabase advisor lint
`function_search_path_mutable`, code 0011). Captured here so a fresh
`alembic upgrade head` lands at the same schema as production.

Effects (all idempotent — safe to re-run):
  - Pin `public._jwt_claim(text).search_path = ''` and rewrite the body
    with fully-qualified names so a caller can't shadow built-ins.
  - Pin `public.custom_access_token_hook(jsonb).search_path = 'public,auth'`
    for the same reason on the JWT auth hook.
  - Create the `internal` schema and move `public.alembic_version` into
    it — keeps PostgREST from exposing the migration table and quiets
    the `rls_disabled_in_public` advisor lint. The app connects over SQL
    (not PostgREST), so the relocation is invisible to clients but
    visible to alembic via `version_table_schema='internal'` in env.py.

Drift note: this lives at 0010 because the alembic file tree slot 0005
is already taken by `0005_super_admin_support`. The Supabase MCP world
numbered it 0005 because slots 0006-0008 had not yet been ported. End
state is identical either way.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_lock_search_paths"
down_revision: str | Sequence[str] | None = "0009_messages_status_ticks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER FUNCTION public._jwt_claim(text) SET search_path = ''")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public._jwt_claim(claim_name text)
        RETURNS text
        LANGUAGE sql
        STABLE
        PARALLEL SAFE
        SET search_path = ''
        AS $$
          SELECT (current_setting('request.jwt.claims', true)::jsonb) ->> claim_name
        $$
        """
    )
    op.execute("CREATE SCHEMA IF NOT EXISTS internal")
    op.execute("ALTER TABLE IF EXISTS public.alembic_version SET SCHEMA internal")
    op.execute(
        "ALTER FUNCTION public.custom_access_token_hook(jsonb) "
        "SET search_path = 'public, auth'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER FUNCTION public.custom_access_token_hook(jsonb) RESET search_path"
    )
    op.execute("ALTER TABLE IF EXISTS internal.alembic_version SET SCHEMA public")
    op.execute("ALTER FUNCTION public._jwt_claim(text) RESET search_path")
