from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, uuid_pk


class WhatsAppTemplate(Base, TimestampMixin):
    """A 360dialog WhatsApp message template, per merchant.

    Distinct from `BotTemplate`/`PromptTemplate` (UC-10 bot prompt config):
    these are Meta-approved message templates required to message a contact
    outside the 24h customer-service window. Lifecycle:
    draft → pending_approval → approved | rejected (synced from 360dialog).
    """

    __tablename__ = "whatsapp_templates"
    __table_args__ = (
        UniqueConstraint("merchant_id", "name", name="uq_whatsapp_templates_merchant_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 360dialog template name (unique per WABA; we append a base36 suffix).
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="UTILITY")
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="it")
    # Functional tag tying a template to a lifecycle step:
    # no_answer_1 | no_answer_2 | reactivation | booking_reminder | first_contact | custom
    purpose: Mapped[str] = mapped_column(String(64), nullable=False, default="custom")
    # draft | pending_approval | approved | rejected
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")

    # Components
    header_type: Mapped[str] = mapped_column(String(16), nullable=False, default="NONE")
    header_text: Mapped[str | None] = mapped_column(Text)
    header_image_url: Mapped[str | None] = mapped_column(String(1024))
    body: Mapped[str] = mapped_column(Text, nullable=False)  # with {{1}}..{{n}}
    footer: Mapped[str | None] = mapped_column(String(120))
    buttons: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Ordered placeholder identifiers extracted from the body, e.g. ["1", "2"].
    variables: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Per-slot source mapping, e.g. {"1": "lead.first_name", "2": "booking.slot"}.
    variable_sources: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)

    # 360dialog / Meta sync
    whatsapp_template_id: Mapped[str | None] = mapped_column(String(128))
    meta_status: Mapped[str | None] = mapped_column(String(32))  # PENDING|APPROVED|REJECTED|...
    meta_quality: Mapped[str | None] = mapped_column(String(16))  # HIGH|MEDIUM|LOW
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta_last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
