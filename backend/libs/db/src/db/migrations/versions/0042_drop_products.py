"""drop products table (catalog removed; bookable offerings live in services)

Revision ID: 0042_drop_products
Revises: 0041_tpl_header_image_path
Create Date: 2026-07-01

The standalone product catalog is removed. Bookable offerings are now modelled
exclusively as `services` (migration 0039); any product/offer info the bot
should cite goes into the Knowledge Base directly. This migration:

  * purges the synthetic catalog RAG corpus (`knowledge_base_docs` with
    source='catalog' and — via ON DELETE CASCADE on `kb_chunks.doc_id` — their
    chunks), so the retriever stops surfacing stale product chunks;
  * drops the `products` table (no inbound FKs reference it; its RLS policy is
    dropped with the table).

Downgrade recreates the `products` table + index + RLS (mirroring 0016) but does
NOT restore data — the catalog content is gone.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_drop_products"
down_revision: str | Sequence[str] | None = "0041_tpl_header_image_path"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Purge the synthetic catalog corpus from RAG. kb_chunks.doc_id carries
    # ON DELETE CASCADE, so deleting the docs removes their chunks too. Plain
    # DML mirrors the 0040 backfill precedent; idempotent (0 rows if clean).
    op.execute("DELETE FROM knowledge_base_docs WHERE source = 'catalog'")

    # Drop the products table — nothing references it; the RLS policy
    # (merchant_isolation_products) is dropped together with the table.
    op.execute("DROP TABLE IF EXISTS products CASCADE")


def downgrade() -> None:
    # Recreate the table + index + RLS (mirrors 0016). Data is NOT restored.
    op.create_table(
        "products",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
        sa.Column(
            "kb_doc_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_base_docs.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
        sa.UniqueConstraint("merchant_id", "handle", name="uq_products_merchant_handle"),
    )
    op.create_index("ix_products_merchant_id", "products", ["merchant_id"])

    predicate = """
        EXISTS (
            SELECT 1 FROM merchants m
            WHERE m.id = products.merchant_id
              AND m.tenant_id = (current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid
              AND (
                  (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id') IS NULL
                  OR m.id = (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')::uuid
              )
        )
    """
    op.execute("ALTER TABLE products ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE products FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY merchant_isolation_products ON products
        USING ({predicate})
        WITH CHECK ({predicate})
        """
    )
