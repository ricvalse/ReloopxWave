"""Repositories for the merchant content layer — policies, FAQ, corrections.

RLS (migration 0016) makes every query tenant-safe automatically; the explicit
`merchant_id` filters on list/upsert mirror the existing KB repo and keep the
SQL readable. All writes `flush()` so callers get generated ids back.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import BotCorrection, FaqEntry, StorePolicy


class StorePolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_merchant(self, merchant_id: UUID) -> StorePolicy | None:
        return (
            await self._session.execute(
                select(StorePolicy).where(StorePolicy.merchant_id == merchant_id)
            )
        ).scalar_one_or_none()

    async def upsert(self, *, merchant_id: UUID, **fields: Any) -> StorePolicy:
        row = await self.get_for_merchant(merchant_id)
        if row is None:
            row = StorePolicy(merchant_id=merchant_id, **fields)
            self._session.add(row)
        else:
            for key, value in fields.items():
                setattr(row, key, value)
        await self._session.flush()
        return row


class FaqRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        merchant_id: UUID,
        question: str,
        answer: str,
        category: str | None = None,
        sort_order: int = 0,
        is_active: bool = True,
    ) -> FaqEntry:
        entry = FaqEntry(
            merchant_id=merchant_id,
            question=question,
            answer=answer,
            category=category,
            sort_order=sort_order,
            is_active=is_active,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def get(self, faq_id: UUID) -> FaqEntry | None:
        return await self._session.get(FaqEntry, faq_id)

    async def list_for_merchant(
        self, merchant_id: UUID, *, active_only: bool = False
    ) -> list[FaqEntry]:
        stmt = select(FaqEntry).where(FaqEntry.merchant_id == merchant_id)
        if active_only:
            stmt = stmt.where(FaqEntry.is_active.is_(True))
        stmt = stmt.order_by(FaqEntry.sort_order, FaqEntry.created_at)
        return list((await self._session.execute(stmt)).scalars())

    async def update(self, faq_id: UUID, **fields: Any) -> FaqEntry | None:
        entry = await self._session.get(FaqEntry, faq_id)
        if entry is None:
            return None
        for key, value in fields.items():
            setattr(entry, key, value)
        await self._session.flush()
        return entry

    async def delete(self, faq_id: UUID) -> bool:
        entry = await self._session.get(FaqEntry, faq_id)
        if entry is None:
            return False
        await self._session.delete(entry)
        await self._session.flush()
        return True


class BotCorrectionRepository:
    """CRUD for the playground response-fix loop (UC-08). Reads are used by both
    the API (management list) and the prompt builder (active-only matching)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        merchant_id: UUID,
        trigger_message: str,
        original_response: str,
        corrected_response: str,
        context: str | None = None,
    ) -> BotCorrection:
        row = BotCorrection(
            merchant_id=merchant_id,
            trigger_message=trigger_message,
            original_response=original_response,
            corrected_response=corrected_response,
            context=context,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, correction_id: UUID) -> BotCorrection | None:
        return await self._session.get(BotCorrection, correction_id)

    async def list_for_merchant(
        self, merchant_id: UUID, *, active_only: bool = False
    ) -> list[BotCorrection]:
        stmt = select(BotCorrection).where(BotCorrection.merchant_id == merchant_id)
        if active_only:
            stmt = stmt.where(BotCorrection.is_active.is_(True))
        stmt = stmt.order_by(BotCorrection.created_at.desc())
        return list((await self._session.execute(stmt)).scalars())

    async def update(self, correction_id: UUID, **fields: Any) -> BotCorrection | None:
        row = await self._session.get(BotCorrection, correction_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._session.flush()
        return row

    async def delete(self, correction_id: UUID) -> bool:
        row = await self._session.get(BotCorrection, correction_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
