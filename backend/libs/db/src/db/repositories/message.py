from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Message


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
            content=content,
            wa_message_id=wa_message_id,
            variant_id=variant_id,
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
            content=content,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            variant_id=variant_id,
        )
        self._session.add(msg)
        await self._session.flush()
        return msg
