from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import KnowledgeBaseDoc


class KnowledgeBaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_doc(
        self,
        *,
        merchant_id: UUID,
        title: str,
        source: str,
        storage_path: str | None = None,
        url: str | None = None,
    ) -> KnowledgeBaseDoc:
        doc = KnowledgeBaseDoc(
            merchant_id=merchant_id,
            title=title,
            source=source,
            storage_path=storage_path,
            url=url,
            status="pending",
        )
        self._session.add(doc)
        await self._session.flush()
        return doc

    async def get(self, doc_id: UUID) -> KnowledgeBaseDoc | None:
        return await self._session.get(KnowledgeBaseDoc, doc_id)

    async def list_for_merchant(self, merchant_id: UUID) -> list[KnowledgeBaseDoc]:
        stmt = (
            select(KnowledgeBaseDoc)
            .where(KnowledgeBaseDoc.merchant_id == merchant_id)
            .order_by(KnowledgeBaseDoc.created_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars())

    async def mark_status(self, doc_id: UUID, *, status: str) -> None:
        doc = await self._session.get(KnowledgeBaseDoc, doc_id)
        if doc is not None:
            doc.status = status
