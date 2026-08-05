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
    # Risposta rimandata perché il merchant era fuori dai suoi orari (0049).
    # Contiene l'istante in cui il bot ha deciso di tacere; lo sweep
    # `resume_after_hours` riparte da lì — sia per capire *cosa* è rimasto
    # senza risposta (gli inbound successivi a questo timestamp) sia per
    # sapere se nel frattempo ha già risposto un operatore.
    #
    # È un marcatore esplicito e non una condizione dedotta ("l'ultimo
    # messaggio è del cliente e nessuno ha replicato") di proposito: quella
    # deduzione pescherebbe anche i thread lasciati in silenzio per tutt'altro
    # motivo — opt-out, handoff, errore LLM — e il bot si metterebbe a
    # rispondere a conversazioni che qualcuno aveva deciso di non toccare.
    off_hours_pending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    # Profilo di conversazione attivo (ADR 0022). Puntatore VIVO, non timbro:
    # un'automazione lo cambia con `set_conversation_profile` e la conversazione
    # ci resta fino a fine episodio, quando torna al profilo `is_default`. NULL =
    # nessun profilo (comportamento identico a prima dei profili).
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversation_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    # FSM state — see ai_core.state_machine.ConvState. NULL = legacy row (treated as GREETING).
    current_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Compressed summary of older turns (S-04 context compressor).
    context_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
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
    # Chi ha prodotto il messaggio. Era una chiave di `meta` (JSONB) letta per
    # stringa da sette file del backend e da un union TypeScript scritto a mano
    # che ne dichiarava sei degli otto valori reali; promossa a colonna con CHECK
    # in 0047 così l'enum arriva al frontend via OpenAPI. Valori: customer |
    # phone | human | ai | agent_action | automation | automation_ai |
    # appointment_reminder.
    sender_type: Mapped[str] = mapped_column(String(24), nullable=False, default="ai")
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
    # --- Attribuzione (0047) — timbri immutabili, scritti all'INSERT ---------
    # Il profilo con cui QUESTO messaggio è stato prodotto (≠ il profilo attivo
    # sulla conversazione, che è un puntatore e può cambiare dopo).
    profile_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # Quale automazione l'ha inviato, e da quale nodo del grafo. Il node_key è
    # ciò che rende leggibile "il DM iniziale converte al 12%, il reminder a 7
    # giorni al 3%", e serve anche come cancello deterministico ("sta rispondendo
    # *a quel* tocco") prima di spendere una chiamata LLM. Nessuna FK: la storia
    # deve sopravvivere alla cancellazione dell'automazione.
    automation_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    automation_node_key: Mapped[str | None] = mapped_column(String(64))
    # Attribuzione conversazionale last-touch: l'ultimo outbound *prima* di
    # questo inbound. Scritta una volta sola, sull'inbound, all'INSERT. NON è il
    # quoted-message di WhatsApp (quello, se servirà, va in `meta.context`).
    # Il reply-rate è un JOIN su questa colonna; il tempo di risposta è la
    # differenza dei due `created_at`.
    reply_to_message_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
