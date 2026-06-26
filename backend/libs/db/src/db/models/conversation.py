from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin, uuid_pk


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    wa_phone_number_id: Mapped[str | None] = mapped_column(String(64))
    wa_contact_phone: Mapped[str | None] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    variant_id: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Timestamp of the last *customer* (inbound) message. Drives the WhatsApp
    # 24h session window: free text inside, approved template outside. Unlike
    # last_message_at (bumped by outbound too), this only moves on inbound.
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Per-thread bot takeover. AND-ed with bot_configs.overrides.bot.auto_reply_enabled
    # in ConversationService.handle_inbound — either off → no assistant turn.
    auto_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Soft-pause with auto-resume (migration 0025). When set in the future the
    # bot stays silent WITHOUT flipping auto_reply, and resumes on its own once
    # the timestamp passes. Set by phone-echo (merchant typed from their app) and
    # by the operator's timed "disattiva AI per X" toggle.
    ai_disabled_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Structured human-handoff state (migration 0025) — drives inbox triage,
    # assignment and SLA. `handoff_summary` is the 1-2 sentence brief the AI
    # writes for the operator when it escalates.
    assigned_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    handoff_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    handoff_summary: Mapped[str | None] = mapped_column(String, nullable=True)
    handoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handoff_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Free-text internal note shown in the inbox detail panel. Per-thread,
    # edited by an agent; NULL when empty. See migration 0012.
    internal_note: Mapped[str | None] = mapped_column(String, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    merchant: Mapped[Merchant] = relationship(back_populates="conversations")  # type: ignore[name-defined]  # noqa: F821
    messages: Mapped[list[Message]] = relationship(back_populates="conversation", passive_deletes=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # role: user | assistant | system | tool | agent (agent = human reply via composer)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # direction: 'in' | 'out' — denormalised from role for cheap filtering
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    # status: pending | sent | delivered | read | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="sent")
    # client_message_id: caller-provided UUID for optimistic reconcile + idempotent retry
    client_message_id: Mapped[str | None] = mapped_column(String(64))
    variant_id: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(120))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    wa_message_id: Mapped[str | None] = mapped_column(String(120), index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
