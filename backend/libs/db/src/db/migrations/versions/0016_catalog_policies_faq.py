"""product catalog + store policies + faq entries

Revision ID: 0016_catalog_policies_faq
Revises: 0015_ghl_marketplace
Create Date: 2026-06-17

Adds the "Amalia-style" merchant content layer the bot draws on:

  * `products` — per-merchant catalog (title/handle/price/variants/images/tags).
    One chunk per product is indexed into `kb_chunks` (via a synthetic
    `knowledge_base_docs` row, `kb_doc_id`) so the existing RAG retriever finds it.
  * `store_policies` — one row per merchant (shipping/returns/payment/…),
    injected into the system prompt.
  * `faq_entries` — structured Q&A, active entries indexed into `kb_chunks`.

All three are merchant-scoped and carry RLS mirroring 0001_initial /
0014 (`merchant_isolation_<table>`): the tenant boundary is enforced via an
EXISTS join through `merchants.tenant_id`; an agency user (no merchant claim)
sees every row under their tenant, a merchant user is pinned to their own.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_catalog_policies_faq"
down_revision: str | Sequence[str] | None = "0015_ghl_marketplace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_MERCHANT_SCOPED = ("products", "store_policies", "faq_entries")


def _ts_columns() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def _merchant_id_col() -> sa.Column:
    return sa.Column(
        "merchant_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
    )


def _kb_doc_col() -> sa.Column:
    return sa.Column(
        "kb_doc_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("knowledge_base_docs.id", ondelete="SET NULL"),
        nullable=True,
    )


def upgrade() -> None:
    # ---- products ----
    op.create_table(
        "products",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        _merchant_id_col(),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("handle", sa.String(160), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("vendor", sa.String(200)),
        sa.Column("product_type", sa.String(120)),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "variants", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "images", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("price", sa.Numeric(10, 2)),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("indexed_at", sa.DateTime(timezone=True)),
        _kb_doc_col(),
        *_ts_columns(),
        sa.UniqueConstraint("merchant_id", "handle", name="uq_products_merchant_handle"),
    )
    op.create_index("ix_products_merchant_id", "products", ["merchant_id"])

    # ---- store_policies (one row per merchant) ----
    op.create_table(
        "store_policies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        _merchant_id_col(),
        sa.Column("shipping_info", sa.Text),
        sa.Column("return_policy", sa.Text),
        sa.Column("payment_methods", sa.Text),
        sa.Column("exchange_policy", sa.Text),
        sa.Column("warranty_info", sa.Text),
        sa.Column("contact_info", sa.Text),
        sa.Column(
            "custom_policies",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_ts_columns(),
        sa.UniqueConstraint("merchant_id", name="uq_store_policies_merchant_id"),
    )
    op.create_index("ix_store_policies_merchant_id", "store_policies", ["merchant_id"])

    # ---- faq_entries ----
    op.create_table(
        "faq_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        _merchant_id_col(),
        sa.Column("question", sa.String(300), nullable=False),
        sa.Column("answer", sa.String(1000), nullable=False),
        sa.Column("category", sa.String(120)),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        _kb_doc_col(),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_ts_columns(),
    )
    op.create_index("ix_faq_entries_merchant_id", "faq_entries", ["merchant_id"])
    op.create_index(
        "ix_faq_entries_merchant_sort", "faq_entries", ["merchant_id", "sort_order"]
    )

    # ---- Row-Level Security (mirror 0001_initial / 0014 merchant-scoped) ----
    def _merchant_scoped_predicate(table: str) -> str:
        return f"""
            EXISTS (
                SELECT 1 FROM merchants m
                WHERE m.id = {table}.merchant_id
                  AND m.tenant_id = (current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid
                  AND (
                      (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id') IS NULL
                      OR m.id = (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')::uuid
                  )
            )
        """

    for table in _NEW_MERCHANT_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        predicate = _merchant_scoped_predicate(table)
        op.execute(
            f"""
            CREATE POLICY merchant_isolation_{table} ON {table}
            USING ({predicate})
            WITH CHECK ({predicate})
            """
        )


def downgrade() -> None:
    for table in reversed(_NEW_MERCHANT_SCOPED):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
