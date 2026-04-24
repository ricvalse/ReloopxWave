"""RAG — indexing and retrieval on Supabase pgvector (section 6.3)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared import get_logger

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class RetrievedChunk:
    chunk_id: UUID
    doc_id: UUID
    content: str
    score: float
    meta: dict[str, Any]


class RAGEngine:
    def __init__(
        self,
        session: AsyncSession,
        embedder: Embedder,
    ) -> None:
        self._session = session
        self._embedder = embedder

    async def retrieve(
        self,
        query: str,
        *,
        merchant_id: UUID,
        top_k: int = 5,
        min_score: float = 0.7,
    ) -> list[RetrievedChunk]:
        embedding = await self._embedder.embed(query)

        # cosine distance operator <=> (lower = closer). Score = 1 - distance.
        rows = await self._session.execute(
            text(
                """
                SELECT id, doc_id, content, meta,
                       1 - (embedding <=> CAST(:q AS vector)) AS score
                FROM kb_chunks
                WHERE merchant_id = :merchant_id
                ORDER BY embedding <=> CAST(:q AS vector)
                LIMIT :k
                """
            ),
            {"q": _vector_literal(embedding), "merchant_id": str(merchant_id), "k": top_k},
        )
        results = [
            RetrievedChunk(
                chunk_id=row.id,
                doc_id=row.doc_id,
                content=row.content,
                score=float(row.score),
                meta=row.meta or {},
            )
            for row in rows.mappings()
        ]
        return [r for r in results if r.score >= min_score]


class Embedder:
    """Thin wrapper so RAGEngine can be unit-tested with a fake."""

    def __init__(self, *, api_key: str, model: str = "text-embedding-3-small") -> None:
        self._api_key = api_key
        self._model = model
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def embed(self, text: str) -> list[float]:
        client = self._get_client()
        resp = await client.embeddings.create(model=self._model, input=text)
        return list(resp.data[0].embedding)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        resp = await client.embeddings.create(model=self._model, input=texts)
        return [list(item.embedding) for item in resp.data]


def _vector_literal(embedding: list[float]) -> str:
    """pgvector accepts a '[x,y,z]' string cast to ::vector."""
    return "[" + ",".join(f"{v:.7f}" for v in embedding) + "]"
