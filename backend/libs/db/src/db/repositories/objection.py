from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Message, Objection


@dataclass(slots=True, frozen=True)
class CategoryCount:
    category: str
    count: int


class ObjectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_for_conversation(
        self,
        *,
        merchant_id: UUID,
        conversation_id: UUID,
        items: list[dict[str, Any]],
    ) -> int:
        """Idempotent — replaces any existing rows for the conversation."""
        await self._session.execute(
            delete(Objection).where(Objection.conversation_id == conversation_id)
        )
        for item in items:
            self._session.add(
                Objection(
                    merchant_id=merchant_id,
                    conversation_id=conversation_id,
                    category=item["category"],
                    summary=item["summary"],
                    quote=item.get("quote"),
                    severity=item.get("severity", "medium"),
                    meta={},
                )
            )
        await self._session.flush()
        return len(items)

    async def list_messages_for_classification(
        self, conversation_id: UUID, *, limit: int = 60
    ) -> list[tuple[str, str]]:
        stmt = (
            select(Message.role, Message.content)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
            .limit(limit)
        )
        return [(r.role, r.content) for r in (await self._session.execute(stmt)).all()]

    async def category_histogram(
        self, *, merchant_id: UUID, since_days: int = 30
    ) -> list[CategoryCount]:
        since = datetime.now(tz=timezone.utc) - timedelta(days=since_days)
        rows = await self._session.execute(
            select(Objection.category, func.count(Objection.id))
            .where(
                Objection.merchant_id == merchant_id,
                Objection.created_at >= since,
            )
            .group_by(Objection.category)
            .order_by(func.count(Objection.id).desc())
        )
        return [CategoryCount(category=c, count=int(n)) for c, n in rows.all()]

    async def recent_samples(
        self, *, merchant_id: UUID, category: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        stmt = (
            select(Objection)
            .where(Objection.merchant_id == merchant_id, Objection.category == category)
            .order_by(Objection.created_at.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [
            {
                "summary": r.summary,
                "quote": r.quote,
                "severity": r.severity,
                "conversation_id": str(r.conversation_id),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
