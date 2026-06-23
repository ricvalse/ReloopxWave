"""Reindex a merchant's catalog + FAQ into the RAG corpus.

Triggered by the catalog router after any product / FAQ write. Each product and
each FAQ pair becomes a single `kb_chunks` row, backed by a synthetic
`knowledge_base_docs` row per corpus (source `catalog` / `faq`) so the existing
retriever surfaces them with zero retriever changes. Idempotent (the indexer
drops the doc's chunks before re-embedding).

Store policies are NOT indexed here — they're injected straight into the system
prompt (see `conversation_service._cascade_system_prompt`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select

from db import (
    FaqRepository,
    ProductRepository,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.models import KnowledgeBaseDoc, Merchant, Product
from shared import get_logger

logger = get_logger(__name__)

_CORPORA = {
    "catalog": "Catalogo prodotti",
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

        catalog_doc = await _ensure_corpus_doc(session, merchant_uuid, "catalog")
        faq_doc = await _ensure_corpus_doc(session, merchant_uuid, "faq")

        products = await ProductRepository(session).list_for_merchant(
            merchant_uuid, active_only=True
        )
        product_records = [
            (_product_text(p), {"kind": "product", "product_id": str(p.id), "handle": p.handle})
            for p in products
        ]
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

        catalog_result = await indexer.index_records(
            merchant_id=merchant_uuid, doc=catalog_doc, records=product_records
        )
        faq_result = await indexer.index_records(
            merchant_id=merchant_uuid, doc=faq_doc, records=faq_records
        )

        # Back-reference + indexed marker so the rows know which corpus doc holds
        # their chunks (useful for cleanup / future structured lookups).
        now = datetime.now(tz=UTC)
        for p in products:
            p.kb_doc_id = catalog_doc.id
            p.indexed_at = now
        for f in faq_entries:
            f.kb_doc_id = faq_doc.id
        await session.flush()

        logger.info(
            "catalog.reindexed",
            merchant_id=merchant_id,
            products=catalog_result.chunk_count,
            faq=faq_result.chunk_count,
        )
        return {
            "merchant_id": merchant_id,
            "status": "indexed",
            "products": catalog_result.chunk_count,
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


def _product_text(p: Product) -> str:
    lines = [p.title]
    if p.product_type:
        lines.append(f"Tipo: {p.product_type}")
    if p.vendor:
        lines.append(f"Marca: {p.vendor}")
    if p.price is not None:
        lines.append(f"Prezzo: {p.price} {p.currency}")
    if p.tags:
        lines.append(f"Tag: {', '.join(p.tags)}")
    variant_labels = [label for v in (p.variants or []) if (label := _variant_label(v))]
    if variant_labels:
        lines.append(f"Varianti: {'; '.join(variant_labels)}")
    if p.description:
        lines.append(p.description)
    images = [str(u).strip() for u in (p.images or []) if str(u).strip()]
    if images:
        lines.append(f"Immagini: {', '.join(images)}")
    return "\n".join(lines)


def _variant_label(v: Any) -> str:
    """Human-readable label for one free-form product variant.

    Variants are merchant/import-defined dicts (Shopify-style), so we pull the
    common keys when present and fall back to joining scalar values — nothing is
    silently dropped from the indexed text.
    """
    if not isinstance(v, dict):
        return str(v).strip()
    name = str(v.get("title") or v.get("name") or "").strip()
    options = [
        str(v[k]).strip()
        for k in ("option1", "option2", "option3")
        if v.get(k) and str(v[k]).strip()
    ]
    label = name or " / ".join(options)
    if not label:
        label = " / ".join(
            str(val).strip()
            for val in v.values()
            if isinstance(val, str | int | float) and str(val).strip()
        )
    price = v.get("price")
    if label and price not in (None, ""):
        label = f"{label} ({price})"
    return label
