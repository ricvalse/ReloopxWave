from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]  # no py.typed marker
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, uuid_pk

EMBEDDING_DIM = 1536  # text-embedding-3-small


class KnowledgeBaseDoc(Base, TimestampMixin):
    """Metadata for KB documents. Binary files live in Supabase Storage."""

    __tablename__ = "knowledge_base_docs"

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # pdf | docx | url | txt
    storage_path: Mapped[str | None] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # Human-readable status note + last failure reason (UC-07), shown in the KB UI.
    status_detail: Mapped[str | None] = mapped_column(String(300))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class KBChunk(Base, TimestampMixin):
    """Chunk of a KB doc plus its embedding vector."""

    __tablename__ = "kb_chunks"

    id: Mapped[uuid.UUID] = uuid_pk()
    doc_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_base_docs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=text("now()")
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class KBGap(Base):
    """Domande dei lead che non hanno trovato risposta nella KB del merchant."""

    __tablename__ = "kb_gaps"

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_text: Mapped[str] = mapped_column(String, nullable=False)
    frequency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
