"""UC-07 — reindex a KB doc.

Triggered by the API router after a new doc is uploaded, or manually from the
merchant panel. Idempotent (Indexer drops existing chunks before re-embedding).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from db import (
    AnalyticsRepository,
    KnowledgeBaseRepository,
    TenantContext,
    session_scope,
    tenant_session,
)
from shared import DomainError, get_logger

logger = get_logger(__name__)


async def reindex_doc(ctx: dict[str, Any], *, doc_id: str) -> dict[str, Any]:
    runtime = ctx["runtime"]
    settings = runtime.settings
    embedder = runtime.embedder
    if embedder is None:
        raise DomainError(
            "no openai key configured — indexing unavailable", error_code="no_embedder"
        )

    # First resolve tenant/merchant for the doc (admin session — we don't know the scope yet).
    from sqlalchemy import select

    from db.models import KnowledgeBaseDoc, Merchant

    async with session_scope() as session:
        stmt = (
            select(KnowledgeBaseDoc, Merchant.tenant_id)
            .join(Merchant, Merchant.id == KnowledgeBaseDoc.merchant_id)
            .where(KnowledgeBaseDoc.id == UUID(doc_id))
        )
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            logger.info("kb.reindex.missing", doc_id=doc_id)
            return {"doc_id": doc_id, "status": "not_found"}
        doc, tenant_id = row
        doc_merchant_id = doc.merchant_id
        source = doc.source
        storage_path = doc.storage_path

    # Fetch bytes from Supabase Storage if needed.
    raw_bytes: bytes | None = None
    if source != "url":
        if not storage_path:
            raise DomainError("doc has no storage_path", error_code="no_storage_path")
        from integrations import SupabaseStorage

        storage = SupabaseStorage(
            project_url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
            bucket=settings.supabase_kb_bucket,
        )
        raw_bytes = await storage.download(storage_path)

    # Now open a tenant session and run the indexer under RLS.
    tenant_ctx = TenantContext(
        tenant_id=tenant_id,
        merchant_id=doc_merchant_id,
        role="worker",
        actor_id=doc_merchant_id,
    )
    async with tenant_session(tenant_ctx) as session:
        from ai_core.rag import Indexer

        kb = KnowledgeBaseRepository(session)
        doc = await kb.get(UUID(doc_id))
        assert doc is not None

        doc.status = "indexing"
        doc.status_detail = "Indicizzazione in corso"
        await session.flush()

        indexer = Indexer(session, embedder)
        try:
            result = await indexer.index_document(
                merchant_id=doc_merchant_id, doc=doc, raw_bytes=raw_bytes
            )
        except Exception as e:
            # Best-effort: registra il motivo del fallimento sul doc così che la
            # UI KB possa mostrarlo (status_detail/last_error troncati ai limiti
            # di colonna). Il flush avviene comunque prima del re-raise.
            err = str(e)
            doc.status = "failed"
            doc.status_detail = "Indicizzazione fallita"
            doc.last_error = err[:1000]
            try:
                await session.flush()
            except Exception:  # non mascherare l'errore originale dell'indexer
                logger.warning("kb.reindex.error_persist_failed", doc_id=doc_id)
            logger.warning("kb.reindex.failed", doc_id=doc_id, error=err)
            await AnalyticsRepository(session).emit(
                tenant_id=tenant_id,
                merchant_id=doc_merchant_id,
                event_type="kb.reindex_failed",
                subject_type="kb_doc",
                subject_id=UUID(doc_id),
                properties={"error": str(e)},
            )
            raise

        # L'Indexer imposta doc.status ("indexed" o "empty"); rifletti l'esito
        # nel dettaglio leggibile e azzera l'eventuale errore precedente.
        if doc.status == "empty":
            doc.status_detail = "Nessun contenuto estraibile"
        else:
            doc.status_detail = f"Indicizzato ({result.chunk_count} chunk)"
        doc.last_error = None
        await session.flush()

        await AnalyticsRepository(session).emit(
            tenant_id=tenant_id,
            merchant_id=doc_merchant_id,
            event_type="kb.reindexed",
            subject_type="kb_doc",
            subject_id=UUID(doc_id),
            properties={"chunk_count": result.chunk_count, "total_chars": result.total_chars},
        )
        return {
            "doc_id": doc_id,
            "status": "indexed",
            "chunk_count": result.chunk_count,
        }
