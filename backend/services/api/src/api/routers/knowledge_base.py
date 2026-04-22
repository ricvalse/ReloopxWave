"""UC-07 — knowledge base endpoints.

Binary uploads go directly to Supabase Storage from the frontend (the
@reloop/supabase-client wrapper handles auth). This router takes the metadata
only: once the file is up, the merchant portal POSTs {storage_path, title,
source}, we create the KnowledgeBaseDoc row and enqueue the reindex job.

URL-based docs don't need Storage at all — just the URL.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.dependencies.session import CurrentContext, DBSession
from db import KnowledgeBaseRepository
from shared import PermissionDeniedError

router = APIRouter()


class KbDocIn(BaseModel):
    title: str
    source: Literal["pdf", "docx", "url", "txt"]
    storage_path: str | None = None
    url: str | None = None


class KbDocOut(BaseModel):
    id: UUID
    title: str
    source: str
    status: str
    chunk_count: int


@router.post("/{merchant_id}/docs", response_model=KbDocOut)
async def create_doc(
    merchant_id: UUID,
    payload: KbDocIn,
    request: Request,
    session: DBSession,
    ctx: CurrentContext,
) -> KbDocOut:
    _assert_merchant_scope(ctx, merchant_id)

    repo = KnowledgeBaseRepository(session)
    doc = await repo.create_doc(
        merchant_id=merchant_id,
        title=payload.title,
        source=payload.source,
        storage_path=payload.storage_path,
        url=payload.url,
    )

    arq = request.app.state.arq
    await arq.enqueue_job("kb_reindex", str(doc.id), _job_id=f"kb:reindex:{doc.id}")

    return KbDocOut(
        id=doc.id,
        title=doc.title,
        source=doc.source,
        status=doc.status,
        chunk_count=doc.chunk_count,
    )


@router.post("/{merchant_id}/docs/{doc_id}/reindex")
async def reindex(
    merchant_id: UUID,
    doc_id: UUID,
    request: Request,
    ctx: CurrentContext,
) -> dict:
    _assert_merchant_scope(ctx, merchant_id)
    arq = request.app.state.arq
    await arq.enqueue_job(
        "kb_reindex", str(doc_id), _job_id=f"kb:reindex:{doc_id}:{int(request.state.ts) if hasattr(request.state, 'ts') else 0}"
    )
    return {"enqueued": True, "doc_id": str(doc_id)}


@router.get("/{merchant_id}/docs", response_model=list[KbDocOut])
async def list_docs(
    merchant_id: UUID,
    session: DBSession,
    ctx: CurrentContext,
) -> list[KbDocOut]:
    _assert_merchant_scope(ctx, merchant_id)
    repo = KnowledgeBaseRepository(session)
    docs = await repo.list_for_merchant(merchant_id)
    return [
        KbDocOut(
            id=d.id,
            title=d.title,
            source=d.source,
            status=d.status,
            chunk_count=d.chunk_count,
        )
        for d in docs
    ]


def _assert_merchant_scope(ctx, merchant_id: UUID) -> None:
    if ctx.merchant_id is not None and ctx.merchant_id != merchant_id:
        raise PermissionDeniedError(
            "Cannot act on another merchant", error_code="cross_merchant_access"
        )
