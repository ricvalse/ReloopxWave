"""UC-07 — knowledge base endpoints.

Binary uploads go directly to Supabase Storage from the frontend (the
@reloop/supabase-client wrapper handles auth). This router takes the metadata
only: once the file is up, the merchant portal POSTs {storage_path, title,
source}, we create the KnowledgeBaseDoc row and enqueue the reindex job.

URL-based docs don't need Storage at all — just the URL.
"""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel

from api.dependencies.session import CurrentContext, DBSession
from db import KnowledgeBaseRepository
from db.models import KnowledgeBaseDoc
from db.session import TenantContext
from integrations import SupabaseStorage
from shared import NotFoundError, PermissionDeniedError, get_logger, get_settings

logger = get_logger(__name__)

router = APIRouter()

# Direct-to-Storage uploads from the browser are scoped by Storage RLS on the
# `merchant_id` claim. That works for a real merchant session but NOT for an
# agency impersonation token if the Supabase data-plane rejects it — so the
# impersonation flow uploads through this server-side proxy instead.
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB, mirrors the frontend cap.


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
    status_detail: str | None = None
    last_error: str | None = None

    @classmethod
    def from_model(cls, d: KnowledgeBaseDoc) -> KbDocOut:
        return cls(
            id=d.id,
            title=d.title,
            source=d.source,
            status=d.status,
            chunk_count=d.chunk_count,
            status_detail=d.status_detail,
            last_error=d.last_error,
        )


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

    return KbDocOut.from_model(doc)


@router.post("/{merchant_id}/upload", response_model=KbDocOut)
async def upload_doc(
    merchant_id: UUID,
    request: Request,
    session: DBSession,
    ctx: CurrentContext,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
) -> KbDocOut:
    """Server-side proxy for KB uploads (used by the agency impersonation flow).

    The file goes up via the Supabase **service role** (a privileged admin op,
    logged with the actor), scoped to `{merchant_id}/...` — the same path layout
    the direct-from-browser path uses, so the indexer is agnostic to which one
    ran. The merchant's own portal keeps uploading direct-to-Storage under RLS.
    """
    _assert_merchant_scope(ctx, merchant_id)

    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise PermissionDeniedError("File too large (max 20 MB)", error_code="kb_file_too_large")

    settings = get_settings()
    storage = SupabaseStorage(
        project_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
        bucket=settings.supabase_kb_bucket,
    )
    filename = file.filename or "documento"
    storage_path = f"{merchant_id}/{int(time.time())}-{_slugify(filename)}"
    await storage.upload_bytes(
        storage_path,
        data,
        content_type=file.content_type or "application/octet-stream",
    )

    logger.info(
        "kb.upload.proxied",
        actor_id=str(ctx.actor_id),
        impersonator_id=str(ctx.impersonator_id) if ctx.impersonator_id else None,
        merchant_id=str(merchant_id),
        storage_path=storage_path,
    )

    repo = KnowledgeBaseRepository(session)
    doc = await repo.create_doc(
        merchant_id=merchant_id,
        title=title,
        source=_infer_source(filename, file.content_type),
        storage_path=storage_path,
        url=None,
    )

    arq = request.app.state.arq
    await arq.enqueue_job("kb_reindex", str(doc.id), _job_id=f"kb:reindex:{doc.id}")

    return KbDocOut.from_model(doc)


@router.post("/{merchant_id}/docs/{doc_id}/reindex")
async def reindex(
    merchant_id: UUID,
    doc_id: UUID,
    request: Request,
    ctx: CurrentContext,
) -> dict[str, Any]:
    _assert_merchant_scope(ctx, merchant_id)
    arq = request.app.state.arq
    # Distinct job id per manual reindex so re-triggers aren't deduped away by
    # ARQ (the previous request.state.ts was never set → always collided on :0).
    await arq.enqueue_job(
        "kb_reindex", str(doc_id), _job_id=f"kb:reindex:{doc_id}:{int(time.time())}"
    )
    return {"enqueued": True, "doc_id": str(doc_id)}


@router.delete("/{merchant_id}/docs/{doc_id}", status_code=204)
async def delete_doc(
    merchant_id: UUID,
    doc_id: UUID,
    session: DBSession,
    ctx: CurrentContext,
) -> None:
    """Rimuove un doc dalla KB (i chunk seguono per FK CASCADE)."""
    _assert_merchant_scope(ctx, merchant_id)
    repo = KnowledgeBaseRepository(session)
    deleted = await repo.delete_doc(merchant_id, doc_id)
    if not deleted:
        raise NotFoundError("Documento non trovato", doc_id=str(doc_id))


@router.get("/{merchant_id}/docs", response_model=list[KbDocOut])
async def list_docs(
    merchant_id: UUID,
    session: DBSession,
    ctx: CurrentContext,
) -> list[KbDocOut]:
    _assert_merchant_scope(ctx, merchant_id)
    repo = KnowledgeBaseRepository(session)
    docs = await repo.list_for_merchant(merchant_id)
    return [KbDocOut.from_model(d) for d in docs]


def _assert_merchant_scope(ctx: TenantContext, merchant_id: UUID) -> None:
    if ctx.merchant_id is not None and ctx.merchant_id != merchant_id:
        raise PermissionDeniedError(
            "Cannot act on another merchant", error_code="cross_merchant_access"
        )


def _slugify(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in ".-" else "-" for c in name.lower())
    return keep.strip("-") or "documento"


def _infer_source(filename: str, content_type: str | None) -> Literal["pdf", "docx", "txt"]:
    lower = filename.lower()
    if (content_type or "") == "application/pdf" or lower.endswith(".pdf"):
        return "pdf"
    docx_ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if (content_type or "") == docx_ct or lower.endswith(".docx"):
        return "docx"
    return "txt"


# ---------------------------------------------------------------------------
# S-02: KB Gap Detection
# ---------------------------------------------------------------------------


class KBGapOut(BaseModel):
    id: UUID
    question_text: str
    frequency: int
    resolved: bool

    @classmethod
    def from_row(cls, row: Any) -> "KBGapOut":
        return cls(
            id=row.id,
            question_text=row.question_text,
            frequency=row.frequency,
            resolved=row.resolved,
        )


@router.get("/merchants/{merchant_id}/knowledge-base/gaps", response_model=list[KBGapOut])
async def list_kb_gaps(
    merchant_id: UUID,
    session: DBSession,
    ctx: CurrentContext,
    resolved: bool = False,
) -> list[KBGapOut]:
    """Return questions the KB could not answer, ordered by frequency."""
    _assert_merchant_scope(ctx, merchant_id)
    from sqlalchemy import text as sqla_text

    rows = await session.execute(
        sqla_text(
            """
            SELECT id, question_text, frequency, resolved
            FROM kb_gaps
            WHERE merchant_id = :mid AND resolved = :resolved
            ORDER BY frequency DESC
            LIMIT 50
            """
        ),
        {"mid": str(merchant_id), "resolved": resolved},
    )
    return [KBGapOut.from_row(r) for r in rows.mappings()]


@router.patch("/merchants/{merchant_id}/knowledge-base/gaps/{gap_id}/resolve")
async def resolve_kb_gap(
    merchant_id: UUID,
    gap_id: UUID,
    session: DBSession,
    ctx: CurrentContext,
) -> dict:
    """Mark a KB gap as resolved (merchant has added the missing content)."""
    _assert_merchant_scope(ctx, merchant_id)
    from sqlalchemy import text as sqla_text

    await session.execute(
        sqla_text(
            "UPDATE kb_gaps SET resolved = true WHERE id = :gap_id AND merchant_id = :mid"
        ),
        {"gap_id": str(gap_id), "mid": str(merchant_id)},
    )
    return {"ok": True}
