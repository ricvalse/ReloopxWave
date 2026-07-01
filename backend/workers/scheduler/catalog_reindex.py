"""Reindex a merchant's FAQ into the RAG corpus.

Triggered by the catalog router after any FAQ write. Each FAQ pair becomes a
single `kb_chunks` row, backed by a synthetic `knowledge_base_docs` row (source
`faq`) so the existing retriever surfaces them with zero retriever changes.
Idempotent (the indexer drops the doc's chunks before re-embedding).

Store policies are NOT indexed here — they're injected straight into the system
prompt (see `conversation_service._cascade_system_prompt`). The product catalog
was removed (migration 0042); product info now lives in the Knowledge Base. The
job name (`catalog_reindex`) is kept for continuity with the queue + router.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from sqlalchemy import select

from db import (
    FaqRepository,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.models import KnowledgeBaseDoc, Merchant
from shared import get_logger

logger = get_logger(__name__)

_CORPORA = {
    "faq": "Domande frequenti",
}


async def reindex_catalog(ctx: dict[str, Any], *, merchant_id: str) -> dict[str, Any]:
    runtime = ctx["runtime"]
    embedder = runtime.embedder
    merchant_uuid = UUID(merchant_id)

    if embedder is None:
        # No OpenAI key (e.g. local dev without secrets): the rows still exist,
        # we just can't embed them. Skip rather than crash the queue.
        logger.info("catalog.reindex.skipped_no_embedder", merchant_id=merchant_id)
        return {"merchant_id": merchant_id, "status": "skipped_no_embedder"}

    async with session_scope() as session:
        tenant_id = (
            await session.execute(select(Merchant.tenant_id).where(Merchant.id == merchant_uuid))
        ).scalar_one_or_none()
    if tenant_id is None:
        logger.info("catalog.reindex.missing_merchant", merchant_id=merchant_id)
        return {"merchant_id": merchant_id, "status": "merchant_not_found"}

    tenant_ctx = TenantContext(
        tenant_id=tenant_id,
        merchant_id=merchant_uuid,
        role="worker",
        actor_id=merchant_uuid,
    )

    async with tenant_session(tenant_ctx) as session:
        from ai_core.rag import Indexer

        indexer = Indexer(session, embedder)

        faq_doc = await _ensure_corpus_doc(session, merchant_uuid, "faq")

        faq_entries = await FaqRepository(session).list_for_merchant(
            merchant_uuid, active_only=True
        )
        faq_records = [
            (
                (f"Categoria: {f.category}\n" if f.category else "")
                + f"Domanda: {f.question}\nRisposta: {f.answer}",
                {"kind": "faq", "faq_id": str(f.id)},
            )
            for f in faq_entries
        ]

        faq_result = await indexer.index_records(
            merchant_id=merchant_uuid, doc=faq_doc, records=faq_records
        )

        # Back-reference so the rows know which corpus doc holds their chunks
        # (useful for cleanup / future structured lookups).
        for f in faq_entries:
            f.kb_doc_id = faq_doc.id
        await session.flush()

        logger.info(
            "catalog.reindexed",
            merchant_id=merchant_id,
            faq=faq_result.chunk_count,
        )
        return {
            "merchant_id": merchant_id,
            "status": "indexed",
            "faq": faq_result.chunk_count,
        }


async def _ensure_corpus_doc(session: Any, merchant_id: UUID, source: str) -> KnowledgeBaseDoc:
    """Find-or-create the synthetic KB doc backing one corpus for a merchant."""
    existing = (
        (
            await session.execute(
                select(KnowledgeBaseDoc).where(
                    KnowledgeBaseDoc.merchant_id == merchant_id,
                    KnowledgeBaseDoc.source == source,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return cast(KnowledgeBaseDoc, existing)
    doc = KnowledgeBaseDoc(
        merchant_id=merchant_id,
        title=_CORPORA[source],
        source=source,
        status="pending",
    )
    session.add(doc)
    await session.flush()
    return doc
