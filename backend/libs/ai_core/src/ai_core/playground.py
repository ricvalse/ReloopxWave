"""UC-08 — playground turn runner.

Reuses the Orchestrator + model router, but deliberately bypasses:
  - WhatsApp send (no outbound message)
  - Conversation/message persistence (no state written)
  - Action dispatch (no GHL side-effects)
  - RAG retrieval is still available (read-only)

This lets a merchant try a system prompt + prompt variant combo against a fake
conversation history before publishing it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ai_core.llm import ChatMessage
from ai_core.orchestrator import ConversationContext, ConversationOrchestrator, OrchestratorResponse
from ai_core.rag import Embedder, RAGEngine
from db import TenantContext, tenant_session
from shared import get_logger

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class PlaygroundMessage:
    role: str  # user | assistant
    content: str


@dataclass(slots=True)
class PlaygroundRequest:
    tenant_id: UUID
    merchant_id: UUID
    system_prompt: str
    history: list[PlaygroundMessage]
    user_message: str
    variant_id: str | None = None
    use_kb: bool = True


@dataclass(slots=True)
class PlaygroundResponse:
    reply_text: str
    actions: list[dict[str, Any]]
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    retrieved_chunks: list[dict[str, Any]]


class PlaygroundRunner:
    def __init__(
        self,
        *,
        orchestrator: ConversationOrchestrator,
        embedder: Embedder | None,
    ) -> None:
        self._orchestrator = orchestrator
        self._embedder = embedder

    async def run(self, req: PlaygroundRequest) -> PlaygroundResponse:
        ctx = TenantContext(
            tenant_id=req.tenant_id,
            merchant_id=req.merchant_id,
            role="playground",
            actor_id=req.merchant_id,
        )

        retrieved: list[dict[str, Any]] = []
        async with tenant_session(ctx) as session:
            kb_chunks = []
            if req.use_kb and self._embedder is not None:
                try:
                    rag = RAGEngine(session, self._embedder)
                    kb_chunks = await rag.retrieve(
                        req.user_message, merchant_id=req.merchant_id, top_k=5, min_score=0.65
                    )
                    retrieved = [
                        {"chunk_id": str(c.chunk_id), "score": c.score, "snippet": c.content[:280]}
                        for c in kb_chunks
                    ]
                except Exception as e:
                    logger.warning("uc08.rag_failed", error=str(e))

            orchestrator_ctx = ConversationContext(
                merchant_id=req.merchant_id,
                tenant_id=req.tenant_id,
                lead_id=None,
                lead_score=0,
                hot_threshold=80,
                system_prompt=req.system_prompt,
                history=[ChatMessage(role=m.role, content=m.content) for m in req.history],
                kb_chunks=kb_chunks,
                variant_id=req.variant_id,
            )

            response: OrchestratorResponse = await self._orchestrator.run(
                orchestrator_ctx, req.user_message
            )

        return PlaygroundResponse(
            reply_text=response.reply_text,
            actions=[
                {"kind": a.kind, "payload": a.payload} for a in response.actions
            ],
            model=response.model,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            latency_ms=response.latency_ms,
            retrieved_chunks=retrieved,
        )
