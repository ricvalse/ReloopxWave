"""drop automation_flows.system_key — every automation is a normal flow (ADR 0015)

The "system flow" concept is removed: automations no longer carry a `system_key`,
so there is no scheduler-driven / trigger-locked / non-deletable class of flow.
Every `automation_flows` row is now a plain, editable, deletable trigger-driven
automation.

This migration just drops the `system_key` column (and the indexes 0028 created
for it: the plain `ix_automation_flows_system_key` and the partial unique
`uq_automation_flows_system_key`). Existing rows are NOT deleted — they survive as
ordinary automations, keeping their `trigger_type`. It reverses the column/index
side of `0028_merge_lifecycle_flows`.

Pure DDL (drop index / drop column), so — unlike 0028 / 0043 — no FORCE ROW LEVEL
SECURITY dance is needed: RLS row policies don't gate schema changes and no rows
are read or written here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_drop_automation_system_key"
down_revision: str | Sequence[str] | None = "0043_enable_system_flows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop indexes defensively (IF EXISTS) so the migration is safe even where the
    # column/indexes were already gone; then drop the column itself.
    op.execute("DROP INDEX IF EXISTS uq_automation_flows_system_key")
    op.execute("DROP INDEX IF EXISTS ix_automation_flows_system_key")
    op.drop_column("automation_flows", "system_key")


def downgrade() -> None:
    # Re-add the column + indexes exactly as 0028_merge_lifecycle_flows defined
    # them (the data is NOT restored — the tags are gone).
    op.add_column("automation_flows", sa.Column("system_key", sa.String(32), nullable=True))
    op.create_index("ix_automation_flows_system_key", "automation_flows", ["system_key"])
    op.execute(
        "CREATE UNIQUE INDEX uq_automation_flows_system_key "
        "ON automation_flows (merchant_id, system_key) WHERE system_key IS NOT NULL"
    )
