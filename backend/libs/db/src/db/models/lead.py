from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin, uuid_pk


class Lead(Base, TimestampMixin):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("merchant_id", "phone", name="uq_leads_merchant_phone"),
        UniqueConstraint("merchant_id", "ghl_contact_id", name="uq_leads_merchant_ghl"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    ghl_contact_id: Mapped[str | None] = mapped_column(String(120))
    name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    sentiment: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    last_interaction_at: Mapped[str | None] = mapped_column(String(64))  # ISO ts cached for filters
    pipeline_stage_id: Mapped[str | None] = mapped_column(String(120))
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    merchant: Mapped["Merchant"] = relationship(back_populates="leads")  # type: ignore[name-defined]  # noqa: F821


class Objection(Base, TimestampMixin):
    __tablename__ = "objections"

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    quote: Mapped[str | None] = mapped_column(String(2000))
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
