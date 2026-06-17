"""ab experiment winner column

Revision ID: 0017_ab_experiment_winner
Revises: 0016_catalog_policies_faq
Create Date: 2026-06-17

Records which variant won when an A/B experiment is stopped (UC-09). Nullable —
draft/running experiments and stops without a declared winner leave it NULL. No
RLS change: `ab_experiments` already carries `merchant_isolation_ab_experiments`
from 0001_initial, and adding a column inherits the table's policy.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_ab_experiment_winner"
down_revision: str | Sequence[str] | None = "0016_catalog_policies_faq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ab_experiments", sa.Column("winner", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("ab_experiments", "winner")
