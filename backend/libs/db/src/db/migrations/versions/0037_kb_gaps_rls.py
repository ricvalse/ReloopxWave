"""kb_gaps — abilita RLS e policy merchant-scoped

Revision ID: 0037_kb_gaps_rls
Revises: 0036_pre_conv_intel
Create Date: 2026-06-27

La migration 0033 ha creato kb_gaps senza ENABLE ROW LEVEL SECURITY né policy,
il che blocca gli INSERT del worker (InsufficientPrivilegeError). Aggiunge la
stessa protezione merchant-scoped usata per kb_chunks e le altre tabelle.
"""

from __future__ import annotations

from alembic import op

revision = "0037_kb_gaps_rls"
down_revision = "0036_pre_conv_intel"
branch_labels = None
depends_on = None

_TABLE = "kb_gaps"

_PREDICATE = """
    EXISTS (
        SELECT 1 FROM merchants m
        WHERE m.id = kb_gaps.merchant_id
          AND m.tenant_id = (current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid
          AND (
              (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id') IS NULL
              OR m.id = (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')::uuid
          )
    )
"""


def upgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY merchant_isolation_{_TABLE} ON {_TABLE}
        USING ({_PREDICATE})
        WITH CHECK ({_PREDICATE})
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS merchant_isolation_{_TABLE} ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
