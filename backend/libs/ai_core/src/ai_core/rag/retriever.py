"""RAG — indexing and retrieval on Supabase pgvector (section 6.3).

S-02 additions:
- HyDE: generate a hypothetical answer to embed instead of the raw query
- LLM re-ranking: re-score retrieved chunks against the full query text
- Freshness decay: weight cosine similarity by chunk recency
- KB gap detection: log queries that didn't find relevant results
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared import get_logger

logger = get_logger(__name__)

# Queries where every returned chunk scores below this threshold are candidates
# for gap detection (the KB has no good answer for this question).
_GAP_SCORE_THRESHOLD = 0.72


@dataclass(slots=True, frozen=True)
class RetrievedChunk:
    chunk_id: UUID
    doc_id: UUID
    content: str
    score: float
    meta: dict[str, Any]


class _LLMClientProto(Protocol):
    """Minimal protocol used by RAGEngine — avoids importing the full llm module."""

    model: str

    async def complete(self, *, messages: list[Any], **kwargs: Any) -> Any: ...


class RAGEngine:
    def __init__(
        self,
        session: AsyncSession,
        embedder: Embedder,
        *,
        llm_client: _LLMClientProto | None = None,
    ) -> None:
        self._session = session
        self._embedder = embedder
        self._llm = llm_client

    async def retrieve(
        self,
        query: str,
        *,
        merchant_id: UUID,
        top_k: int = 5,
        min_score: float = 0.7,
        hyde_enabled: bool = True,
        rerank_enabled: bool = True,
        rerank_top_k: int = 5,
        freshness_decay: float = 0.01,
        log_gaps: bool = True,
    ) -> list[RetrievedChunk]:
        """Retrieve the most relevant KB chunks for the given query.

        Pipeline:
        1. HyDE: embed a hypothetical answer (or the raw query if LLM unavailable)
        2. pgvector search with freshness decay weighting (top_k * 4 candidates)
        3. Filter by min_score
        4. LLM re-ranking: re-score candidates and keep rerank_top_k best
        5. Gap detection: log queries with no good results
        """
        # Step 1 — HyDE
        search_text = await self._hyde(query, enabled=hyde_enabled)
        embedding = await self._embedder.embed(search_text)

        # Step 2 — vector search with freshness decay
        fetch_k = top_k * 4 if rerank_enabled and self._llm else top_k
        candidates = await self._vector_search(
            embedding, merchant_id=merchant_id, k=fetch_k, freshness_decay=freshness_decay
        )

        # Step 3 — min_score filter
        candidates = [c for c in candidates if c.score >= min_score]

        if not candidates:
            if log_gaps and query.strip():
                await self._log_gap(merchant_id, query)
            return []

        # Step 4 — LLM re-ranking
        if rerank_enabled and self._llm and len(candidates) > rerank_top_k:
            candidates = await self._rerank(query, candidates, top_k=rerank_top_k)
        else:
            candidates = candidates[:top_k]

        # Step 5 — gap detection when best score is still low
        if log_gaps and candidates and candidates[0].score < _GAP_SCORE_THRESHOLD:
            await self._log_gap(merchant_id, query)

        return candidates

    async def _hyde(self, query: str, *, enabled: bool) -> str:
        """Generate a hypothetical answer and use it as the embedding query."""
        if not enabled or self._llm is None:
            return query
        try:
            from ai_core.llm import ChatMessage

            resp = await self._llm.complete(
                messages=[
                    ChatMessage(
                        role="system",
                        content="Sei un esperto. Rispondi brevemente alla domanda in italiano.",
                    ),
                    ChatMessage(role="user", content=query),
                ],
                max_tokens=150,
            )
            hypothetical = getattr(resp, "content", None) or query
            return hypothetical if hypothetical.strip() else query
        except Exception as e:
            logger.debug("rag.hyde_failed", error=str(e))
            return query

    async def _vector_search(
        self,
        embedding: list[float],
        *,
        merchant_id: UUID,
        k: int,
        freshness_decay: float,
    ) -> list[RetrievedChunk]:
        vec = _vector_literal(embedding)
        rows = await self._session.execute(
            text(
                """
                SELECT id, doc_id, content, meta,
                       (1 - (embedding <=> CAST(:q AS vector)))
                       * EXP(-CAST(:decay AS float8) * EXTRACT(EPOCH FROM (NOW() - COALESCE(last_updated_at, created_at))) / 86400.0)
                       AS score
                FROM kb_chunks
                WHERE merchant_id = :merchant_id
                ORDER BY score DESC
                LIMIT :k
                """
            ),
            {
                "q": vec,
                "merchant_id": str(merchant_id),
                "k": k,
                "decay": freshness_decay,
            },
        )
        return [
            RetrievedChunk(
                chunk_id=row.id,
                doc_id=row.doc_id,
                content=row.content,
                score=float(row.score),
                meta=row.meta or {},
            )
            for row in rows.mappings()
        ]

    async def _rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        *,
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Re-rank candidates using a single LLM call. Fallback = original order."""
        if self._llm is None:
            return candidates[:top_k]
        numbered = "\n".join(f"[{i}] {c.content[:300]}" for i, c in enumerate(candidates))
        prompt = (
            f"Domanda: {query}\n\n"
            f"Frammenti KB numerati:\n{numbered}\n\n"
            "Restituisci SOLO un array JSON degli indici ordinati dal più rilevante "
            f"al meno rilevante, includi solo i {top_k} migliori. Esempio: [2,0,4]"
        )
        try:
            from ai_core.llm import ChatMessage

            resp = await self._llm.complete(
                messages=[ChatMessage(role="user", content=prompt)],
                response_format={"type": "json_object"},
                max_tokens=80,
            )
            raw = getattr(resp, "content", "[]")
            # The model may return {"indices": [...]} or directly [...]
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                indices = next(
                    (v for v in parsed.values() if isinstance(v, list)), list(range(len(candidates)))
                )
            else:
                indices = parsed
            result = []
            seen: set[int] = set()
            for idx in indices:
                if isinstance(idx, int) and 0 <= idx < len(candidates) and idx not in seen:
                    result.append(candidates[idx])
                    seen.add(idx)
                if len(result) >= top_k:
                    break
            # Fill remainder with original order if LLM returned fewer indices
            if len(result) < top_k:
                for i, c in enumerate(candidates):
                    if i not in seen:
                        result.append(c)
                    if len(result) >= top_k:
                        break
            return result
        except Exception as e:
            logger.debug("rag.rerank_failed", error=str(e))
            return candidates[:top_k]

    async def _log_gap(self, merchant_id: UUID, question: str) -> None:
        """Upsert a kb_gap row: increment frequency if same question seen before."""
        try:
            await self._session.execute(
                text(
                    """
                    INSERT INTO kb_gaps (merchant_id, question_text, frequency, last_seen_at)
                    VALUES (:merchant_id, :question, 1, now())
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"merchant_id": str(merchant_id), "question": question[:1000]},
            )
        except Exception as e:
            logger.debug("rag.gap_log_failed", error=str(e))


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
