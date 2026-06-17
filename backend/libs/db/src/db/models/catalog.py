"""Merchant content the bot draws on — product catalog, store policies, FAQ.

All three are merchant-scoped and RLS-protected (see migration 0016). Products
and FAQ are indexed into `kb_chunks` for RAG retrieval via a synthetic
`KnowledgeBaseDoc` per corpus (the `kb_doc_id` back-reference); store policies
are injected straight into the system prompt (short, always-relevant facts).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, uuid_pk


class Product(Base, TimestampMixin):
    """A catalog product the bot can propose / cite. One chunk per product is
    pushed into `kb_chunks` so the existing RAG retriever surfaces it."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("merchant_id", "handle", name="uq_products_merchant_handle"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    handle: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    vendor: Mapped[str | None] = mapped_column(String(200))
    product_type: Mapped[str | None] = mapped_column(String(120))
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    variants: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    images: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Last time this product was pushed into kb_chunks (None = never indexed).
    indexed_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True))
    # Synthetic KB doc backing this merchant's catalog chunks.
    kb_doc_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_base_docs.id", ondelete="SET NULL"),
    )


class StorePolicy(Base, TimestampMixin):
    """Operational policies (shipping/returns/payment/…). One row per merchant,
    injected into the system prompt so the bot answers policy questions."""

    __tablename__ = "store_policies"

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    shipping_info: Mapped[str | None] = mapped_column(Text)
    return_policy: Mapped[str | None] = mapped_column(Text)
    payment_methods: Mapped[str | None] = mapped_column(Text)
    exchange_policy: Mapped[str | None] = mapped_column(Text)
    warranty_info: Mapped[str | None] = mapped_column(Text)
    contact_info: Mapped[str | None] = mapped_column(Text)
    # Free-form extra policies: list of {title, body}.
    custom_policies: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class FaqEntry(Base, TimestampMixin):
    """A structured Q&A pair. Active entries are indexed into `kb_chunks`."""

    __tablename__ = "faq_entries"
    __table_args__ = (
        Index("ix_faq_entries_merchant_sort", "merchant_id", "sort_order"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question: Mapped[str] = mapped_column(String(300), nullable=False)
    answer: Mapped[str] = mapped_column(String(1000), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    kb_doc_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_base_docs.id", ondelete="SET NULL"),
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
