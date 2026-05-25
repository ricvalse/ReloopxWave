from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Message


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_wa_message_id(self, wa_message_id: str) -> Message | None:
        """Return the message with this WhatsApp id, or None.

        Used to make inbound persistence idempotent: the WA webhook may be
        re-delivered and the worker job re-run, so we skip the insert when the
        message is already stored. `wa_message_id` is indexed for this lookup.
        """
        stmt = select(Message).where(Message.wa_message_id == wa_message_id).limit(1)
        return (await self._session.execute(stmt)).scalars().first()

    async def list_history(self, conversation_id: UUID, *, limit: int = 30) -> list[Message]:
        """Returns the last `limit` messages in chronological (oldest-first) order."""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        rows = list((await self._session.execute(stmt)).scalars())
        rows.reverse()
        return rows

    async def persist_user_message(
        self,
        *,
        conversation_id: UUID,
        merchant_id: UUID,
        content: str,
        wa_message_id: str | None,
        variant_id: str | None = None,
    ) -> Message:
        msg = Message(
            conversation_id=conversation_id,
            merchant_id=merchant_id,
            role="user",
            direction="in",
            content=content,
            wa_message_id=wa_message_id,
            variant_id=variant_id,
        )
        self._session.add(msg)
        await self._session.flush()
        return msg

    async def persist_phone_echo_message(
        self,
        *,
        conversation_id: UUID,
        merchant_id: UUID,
        content: str,
        wa_message_id: str,
    ) -> Message:
        """Outbound message that originated from the merchant's phone Business App.

        Stored as `role='agent', direction='out', status='sent'` — the message has
        already been delivered by the time we hear about it, so we skip the
        pending→sent state machine entirely. `meta.sender_type='phone'` lets the
        UI distinguish phone-typed replies from composer-typed ones.
        """
        msg = Message(
            conversation_id=conversation_id,
            merchant_id=merchant_id,
            role="agent",
            direction="out",
            status="sent",
            content=content,
            wa_message_id=wa_message_id,
            meta={"sender_type": "phone"},
        )
        self._session.add(msg)
        await self._session.flush()
        return msg

    async def persist_assistant_message(
        self,
        *,
        conversation_id: UUID,
        merchant_id: UUID,
        content: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        variant_id: str | None = None,
    ) -> Message:
        msg = Message(
            conversation_id=conversation_id,
            merchant_id=merchant_id,
            role="assistant",
            direction="out",
            content=content,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            variant_id=variant_id,
            meta={"sender_type": "ai"},
        )
        self._session.add(msg)
        await self._session.flush()
        return msg
