"""Collect booked / qualified conversations for a tenant.

Pulls conversations whose outcome suggests the agent handled them well
enough to be a positive training example. Booked is the strongest signal;
qualified-without-booking is a weaker but still useful positive.

Returns lists of `(user_turn, assistant_turn)` pairs — ready for the
anonymizer + export step. Does not touch OpenAI or any external API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Conversation, Lead, Merchant, Message


@dataclass(slots=True, frozen=True)
class TrainingPair:
    conversation_id: UUID
    user: str
    assistant: str


async def collect_training_pairs(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    since: datetime,
    until: datetime,
    limit_conversations: int = 2000,
) -> list[TrainingPair]:
    """Find conversations in the window that resulted in a positive outcome,
    then zip user/assistant turns in order. Assumes `messages.role`
    in {user, assistant, system, tool} — anything else is skipped.

    The "positive outcome" signal today is `conversations.status == 'closed'`
    AND `leads.status IN ('booked', 'qualified')`. The FT pipeline's eval
    step compares against the current baseline so this coarse filter is
    the floor — a more selective filter is a quality tuning knob.
    """
    conv_stmt = (
        select(Conversation.id)
        .join(Merchant, Merchant.id == Conversation.merchant_id)
        .join(Lead, Lead.id == Conversation.lead_id, isouter=True)
        .where(
            Merchant.tenant_id == tenant_id,
            Conversation.status == "closed",
            Conversation.last_message_at.between(since, until),
            Lead.status.in_(("booked", "qualified")),
        )
        .order_by(Conversation.last_message_at.desc())
        .limit(limit_conversations)
    )
    conversation_ids = [row[0] for row in (await session.execute(conv_stmt)).all()]

    if not conversation_ids:
        return []

    msg_stmt = (
        select(Message)
        .where(Message.conversation_id.in_(conversation_ids))
        .order_by(Message.conversation_id, Message.created_at.asc())
    )
    rows = (await session.execute(msg_stmt)).scalars().all()

    pairs: list[TrainingPair] = []
    # Walk each conversation, pair user→assistant turns in order.
    buffer: dict[UUID, list[Message]] = {}
    for msg in rows:
        buffer.setdefault(msg.conversation_id, []).append(msg)

    for conv_id, msgs in buffer.items():
        pending_user: str | None = None
        for m in msgs:
            if m.role == "user":
                pending_user = m.content
            elif m.role == "assistant" and pending_user is not None:
                pairs.append(
                    TrainingPair(
                        conversation_id=conv_id,
                        user=pending_user,
                        assistant=m.content,
                    )
                )
                pending_user = None

    return pairs
