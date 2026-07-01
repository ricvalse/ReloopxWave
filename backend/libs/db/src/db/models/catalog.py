"""Merchant content the bot draws on — store policies, FAQ, bot corrections.

All merchant-scoped and RLS-protected (see migration 0016). FAQ entries are
indexed into `kb_chunks` for RAG retrieval via a synthetic `KnowledgeBaseDoc`
(the `kb_doc_id` back-reference); store policies are injected straight into the
system prompt (short, always-relevant facts). The product catalog that used to
live here was removed (migration 0042) — bookable offerings are modelled as
`services` and any product info the bot should cite goes into the Knowledge
Base directly.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, uuid_pk


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
    __table_args__ = (Index("ix_faq_entries_merchant_sort", "merchant_id", "sort_order"),)

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


class BotCorrection(Base, TimestampMixin):
    """A merchant-authored fix for a bad bot reply (UC-08 playground loop).

    Captured when the merchant edits a response in the playground: the customer
    message that triggered it, what the bot said, and what it should have said.
    Active rows are matched (word-overlap) against the current customer message
    and the top few are injected into the system prompt as mandatory overrides
    — so the bot "learns" the fix immediately, scoped to THIS merchant only.
    """

    __tablename__ = "bot_corrections"
    __table_args__ = (Index("ix_bot_corrections_merchant_active", "merchant_id", "is_active"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trigger_message: Mapped[str] = mapped_column(Text, nullable=False)
    original_response: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_response: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
