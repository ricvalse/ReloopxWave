"""merchant-level RLS for analytics_events + ft_models

Revision ID: 0019_analytics_merchant_rls
Revises: 0018_appointments
Create Date: 2026-06-17

`analytics_events` and `ft_models` shipped as TENANT-scoped (0001): their
`tenant_isolation_*` policies check only `tenant_id`, so a *merchant* user could
read every other merchant's analytics/FT rows inside the same agency tenant
(UC-11 / CC-TENANCY intra-tenant leak). Both tables already carry a direct
`merchant_id` column (nullable for tenant-level rows), so we swap the tenant-only
policy for the same tenant-OR-merchant predicate used by `users` in 0001:

  tenant matches AND (
      no merchant claim (agency user)         -- sees all rows in the tenant
      OR row.merchant_id IS NULL              -- tenant-level rows, visible in tenant
      OR claim.merchant_id == row.merchant_id -- merchant user, own rows only
  )

Inserts still pass WITH CHECK: events/models are written under a session whose
merchant claim equals the row's merchant_id (or both NULL for agency-level rows).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019_analytics_merchant_rls"
down_revision: str | Sequence[str] | None = "0018_appointments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("analytics_events", "ft_models")


def _tenant_or_merchant_predicate(table: str) -> str:
    return f"""
        ({table}.tenant_id = (current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid)
        AND (
            (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id') IS NULL
            OR {table}.merchant_id IS NULL
            OR (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')::uuid
               = {table}.merchant_id
        )
    """


def _tenant_only_predicate(table: str) -> str:
    return (
        "(current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid "
        f"= {table}.tenant_id"
    )


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        predicate = _tenant_or_merchant_predicate(table)
        op.execute(
            f"""
            CREATE POLICY tenant_or_merchant_isolation_{table} ON {table}
            USING ({predicate})
            WITH CHECK ({predicate})
            """
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_or_merchant_isolation_{table} ON {table}")
        predicate = _tenant_only_predicate(table)
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING ({predicate})
            WITH CHECK ({predicate})
            """
        )
