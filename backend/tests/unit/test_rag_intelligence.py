"""Unit tests for S-02 RAG intelligence (HyDE, re-ranking, gap detection)."""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_core.rag.retriever import RAGEngine, RetrievedChunk


def _chunk(score: float = 0.8, content: str = "chunk content") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        doc_id=uuid.uuid4(),
        content=content,
        score=score,
        meta={},
    )


class FakeEmbedder:
    def __init__(self, vector: list[float] | None = None) -> None:
        self.calls: list[str] = []
        self._vector = vector or [0.1, 0.2, 0.3]

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return self._vector


class FakeLLM:
    def __init__(self, response_content: str = '{"indices":[0,1,2]}') -> None:
        self.calls: list[dict] = []
        self._response_content = response_content
        self.model = "gpt-4.1-nano"

    async def complete(self, *, messages: list[Any], **kwargs: Any) -> Any:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        result = MagicMock()
        result.content = self._response_content
        return result


class FakeSession:
    def __init__(self, chunks: list[RetrievedChunk] | None = None) -> None:
        self._chunks = chunks or []
        self.gap_inserts: list[dict] = []

    async def execute(self, stmt: Any, params: dict | None = None) -> Any:
        params = params or {}
        # Detect gap insert
        stmt_str = str(stmt)
        if "kb_gaps" in stmt_str and "INSERT" in stmt_str:
            self.gap_inserts.append(params)
            result = MagicMock()
            result.mappings.return_value = []
            return result
        # Vector search
        result = MagicMock()

        def _make_row(c: RetrievedChunk) -> Any:
            row = MagicMock()
            row.id = c.chunk_id
            row.doc_id = c.doc_id
            row.content = c.content
            row.score = c.score
            row.meta = c.meta
            return row

        result.mappings.return_value = [_make_row(c) for c in self._chunks]
        return result


class TestHyDE:
    async def test_hyde_uses_hypothetical_answer_as_embedding_query(self):
        embedder = FakeEmbedder()
        llm = FakeLLM()
        llm._response_content = "Risposta ipotetica"

        session = FakeSession()
        engine = RAGEngine(session, embedder, llm_client=llm)  # type: ignore[arg-type]

        # Intercept: make LLM return a hypothetical
        llm.complete = AsyncMock(return_value=MagicMock(content="risposta ipotetica"))

        await engine.retrieve(
            "qual è il prezzo?",
            merchant_id=uuid.uuid4(),
            top_k=3,
            min_score=0.0,
            hyde_enabled=True,
            rerank_enabled=False,
        )
        # The embedder should have received the hypothetical, not the raw query
        assert embedder.calls[0] == "risposta ipotetica"

    async def test_hyde_falls_back_to_raw_query_on_llm_error(self):
        embedder = FakeEmbedder()
        llm = FakeLLM()
        llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))

        session = FakeSession()
        engine = RAGEngine(session, embedder, llm_client=llm)  # type: ignore[arg-type]

        await engine.retrieve(
            "qual è il prezzo?",
            merchant_id=uuid.uuid4(),
            top_k=3,
            min_score=0.0,
            hyde_enabled=True,
            rerank_enabled=False,
        )
        assert embedder.calls[0] == "qual è il prezzo?"

    async def test_hyde_disabled_uses_raw_query(self):
        embedder = FakeEmbedder()
        llm = FakeLLM()
        session = FakeSession()
        engine = RAGEngine(session, embedder, llm_client=llm)  # type: ignore[arg-type]

        await engine.retrieve(
            "raw query",
            merchant_id=uuid.uuid4(),
            top_k=3,
            min_score=0.0,
            hyde_enabled=False,
            rerank_enabled=False,
        )
        assert embedder.calls[0] == "raw query"


class TestReranking:
    async def test_rerank_reorders_chunks(self):
        chunks = [_chunk(0.8, "A"), _chunk(0.85, "B"), _chunk(0.75, "C")]
        session = FakeSession(chunks)
        embedder = FakeEmbedder()
        # LLM says: prefer index 2, then 0, then 1
        llm = FakeLLM('[2, 0, 1]')
        engine = RAGEngine(session, embedder, llm_client=llm)  # type: ignore[arg-type]

        result = await engine.retrieve(
            "q",
            merchant_id=uuid.uuid4(),
            top_k=2,
            min_score=0.0,
            hyde_enabled=False,
            rerank_enabled=True,
            rerank_top_k=2,
        )
        assert result[0].content == "C"
        assert result[1].content == "A"

    async def test_rerank_falls_back_on_llm_error(self):
        chunks = [_chunk(0.8, "A"), _chunk(0.85, "B"), _chunk(0.75, "C")]
        session = FakeSession(chunks)
        embedder = FakeEmbedder()
        llm = FakeLLM()
        llm.complete = AsyncMock(side_effect=RuntimeError("boom"))
        engine = RAGEngine(session, embedder, llm_client=llm)  # type: ignore[arg-type]

        result = await engine.retrieve(
            "q",
            merchant_id=uuid.uuid4(),
            top_k=2,
            min_score=0.0,
            hyde_enabled=False,
            rerank_enabled=True,
            rerank_top_k=2,
        )
        assert len(result) == 2  # falls back to original order, top 2


class TestGapDetection:
    async def test_gap_logged_when_no_chunks_found(self):
        session = FakeSession(chunks=[])  # empty → 0 results
        embedder = FakeEmbedder()
        engine = RAGEngine(session, embedder)

        await engine.retrieve(
            "domanda senza risposta",
            merchant_id=uuid.uuid4(),
            top_k=5,
            min_score=0.5,
            hyde_enabled=False,
            rerank_enabled=False,
            log_gaps=True,
        )
        assert len(session.gap_inserts) == 1
        assert "domanda senza risposta" in session.gap_inserts[0]["question"]

    async def test_gap_not_logged_when_disabled(self):
        session = FakeSession(chunks=[])
        embedder = FakeEmbedder()
        engine = RAGEngine(session, embedder)

        await engine.retrieve(
            "domanda",
            merchant_id=uuid.uuid4(),
            top_k=5,
            min_score=0.5,
            hyde_enabled=False,
            rerank_enabled=False,
            log_gaps=False,
        )
        assert len(session.gap_inserts) == 0
