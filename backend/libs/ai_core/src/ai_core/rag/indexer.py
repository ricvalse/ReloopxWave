"""KB indexer — parses PDF/DOCX/URL/text, chunks, embeds, and upserts to pgvector.

Keeps external parser deps (pypdf, python-docx, beautifulsoup4) behind lazy
imports so the ai_core package installs without them at runtime if a deployment
only needs retrieval.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import httpx
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ai_core.rag.chunker import chunk_text
from ai_core.rag.retriever import Embedder
from db.models import KBChunk, KnowledgeBaseDoc
from shared import DomainError, get_logger

logger = get_logger(__name__)

DocKind = Literal["pdf", "docx", "url", "txt"]


@dataclass(slots=True, frozen=True)
class IndexResult:
    doc_id: UUID
    chunk_count: int
    total_chars: int


class Indexer:
    def __init__(self, session: AsyncSession, embedder: Embedder) -> None:
        self._session = session
        self._embedder = embedder

    async def index_document(
        self,
        *,
        merchant_id: UUID,
        doc: KnowledgeBaseDoc,
        raw_bytes: bytes | None = None,
    ) -> IndexResult:
        """Idempotent re-index: drops existing chunks for the doc, then re-embeds.

        `raw_bytes` is supplied by the caller (who fetched the file from Supabase
        Storage). For URL docs we fetch inline here since there's nothing to upload.
        """
        if doc.source == "url":
            text = await extract_text_from_url(doc.url or "")
        elif raw_bytes is None:
            raise DomainError(
                "indexer requires raw_bytes for non-URL docs",
                error_code="indexer_missing_bytes",
            )
        else:
            text = extract_text_from_bytes(doc.source, raw_bytes)

        chunks = chunk_text(text)
        if not chunks:
            doc.status = "empty"
            doc.chunk_count = 0
            return IndexResult(doc_id=doc.id, chunk_count=0, total_chars=0)

        # Drop existing chunks first so repeated indexing is idempotent.
        await self._session.execute(delete(KBChunk).where(KBChunk.doc_id == doc.id))

        # Embed in a single batch request — cheap both in latency and cost.
        embeddings = await self._embedder.embed_batch([c.content for c in chunks])

        for chunk, emb in zip(chunks, embeddings, strict=True):
            self._session.add(
                KBChunk(
                    doc_id=doc.id,
                    merchant_id=merchant_id,
                    chunk_index=chunk.index,
                    content=chunk.content,
                    embedding=emb,
                    tokens=chunk.char_count // 4,
                    meta={},
                )
            )

        doc.status = "indexed"
        doc.chunk_count = len(chunks)
        await self._session.flush()
        return IndexResult(
            doc_id=doc.id,
            chunk_count=len(chunks),
            total_chars=sum(c.char_count for c in chunks),
        )


# ---- Extractors ----------------------------------------------------------

def extract_text_from_bytes(source: str, raw: bytes) -> str:
    if source == "pdf":
        return _extract_pdf(raw)
    if source == "docx":
        return _extract_docx(raw)
    if source == "txt":
        return raw.decode("utf-8", errors="replace")
    raise DomainError(f"Unsupported source: {source}", error_code="unsupported_source")


def _extract_pdf(raw: bytes) -> str:
    from pypdf import PdfReader  # lazy

    reader = PdfReader(io.BytesIO(raw))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)


def _extract_docx(raw: bytes) -> str:
    from docx import Document  # lazy — python-docx exposes `docx`

    doc = Document(io.BytesIO(raw))
    return "\n\n".join(p.text for p in doc.paragraphs if p.text)


async def extract_text_from_url(url: str) -> str:
    if not url:
        raise DomainError("empty url", error_code="empty_url")
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "ReloopAI-KB-Indexer/0.1"})
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "application/pdf" in content_type:
            return _extract_pdf(resp.content)
        if "html" in content_type:
            return _extract_html(resp.text)
        return resp.text


def _extract_html(html: str) -> str:
    from bs4 import BeautifulSoup  # lazy

    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "nav", "header", "footer"]):
        node.decompose()
    text = soup.get_text(separator="\n")
    return "\n\n".join(line.strip() for line in text.splitlines() if line.strip())
