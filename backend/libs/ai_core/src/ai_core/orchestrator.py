"""ConversationOrchestrator — the single entry point for every conversation turn.

Flow (section 6.1):
  build context -> select model -> call LLM -> parse structured output -> return actions.

The response is constrained by a Pydantic schema so downstream workers can
dispatch book_slot / move_pipeline / update_score / escalate_human without
parsing free-form text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ai_core.llm import ChatMessage, LLMClient
from ai_core.rag import RetrievedChunk
from ai_core.router import ModelRouter, RoutingRequest
from shared import get_logger

logger = get_logger(__name__)


ActionKind = Literal["book_slot", "move_pipeline", "update_score", "escalate_human", "none"]


class OrchestratorAction(BaseModel):
    kind: ActionKind
    payload: dict[str, Any] = Field(default_factory=dict)


class OrchestratorResponse(BaseModel):
    reply_text: str
    actions: list[OrchestratorAction] = Field(default_factory=list)
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


@dataclass(slots=True)
class ConversationContext:
    merchant_id: UUID
    tenant_id: UUID
    lead_id: UUID | None
    lead_score: int
    hot_threshold: int
    system_prompt: str
    history: list[ChatMessage] = field(default_factory=list)
    kb_chunks: list[RetrievedChunk] = field(default_factory=list)
    variant_id: str | None = None


class ConversationOrchestrator:
    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def run(self, ctx: ConversationContext, user_message: str) -> OrchestratorResponse:
        messages = self._build_messages(ctx, user_message)
        context_tokens = sum(len(m.content) for m in messages) // 4  # rough estimate

        req = RoutingRequest(
            merchant_id=ctx.merchant_id,
            tenant_id=ctx.tenant_id,
            context_tokens=context_tokens,
            turn_count=len(ctx.history),
            lead_score=ctx.lead_score,
            hot_threshold=ctx.hot_threshold,
            escalate_keywords_matched=_has_critical_objection(user_message),
        )
        client: LLMClient = await self._router.select(req)

        try:
            result = await client.complete(
                messages=messages,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.warning("orchestrator.llm_failed", error=str(e), model=client.model)
            fallback = await self._router.fallback()
            if fallback is None:
                raise
            result = await fallback.complete(messages=messages)

        parsed = _parse_structured(result.content)
        return OrchestratorResponse(
            reply_text=parsed.reply_text,
            actions=parsed.actions,
            model=result.model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            latency_ms=result.latency_ms,
        )

    def _build_messages(self, ctx: ConversationContext, user_message: str) -> list[ChatMessage]:
        system_parts = [ctx.system_prompt, _RESPONSE_SCHEMA_HINT]
        if ctx.kb_chunks:
            kb_snippet = "\n---\n".join(
                f"[{i + 1}] {c.content}" for i, c in enumerate(ctx.kb_chunks)
            )
            system_parts.append(f"Knowledge base context:\n{kb_snippet}")
        messages = [ChatMessage(role="system", content="\n\n".join(system_parts))]
        messages.extend(ctx.history)
        messages.append(ChatMessage(role="user", content=user_message))
        return messages


class _StructuredResponse(BaseModel):
    reply_text: str
    actions: list[OrchestratorAction] = Field(default_factory=list)


_RESPONSE_SCHEMA_HINT = (
    "Rispondi SEMPRE con un JSON che rispetta esattamente questo schema:\n"
    "{\n"
    '  "reply_text": "<testo da inviare all\'utente>",\n'
    '  "actions": [\n'
    '    {"kind": "book_slot|move_pipeline|update_score|escalate_human|none", "payload": {}}\n'
    "  ]\n"
    "}\n"
    "`actions` può essere lista vuota. `reply_text` non deve mai essere vuoto."
)

CRITICAL_KEYWORDS = (
    "reclamo",
    "avvocato",
    "truffa",
    "rimborso immediato",
    "denuncia",
    "concorrenza",
)


def _has_critical_objection(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in CRITICAL_KEYWORDS)


def _parse_structured(raw: str) -> _StructuredResponse:
    try:
        return _StructuredResponse.model_validate_json(raw)
    except Exception:
        # Graceful fallback: treat the whole response as plain text, no actions.
        return _StructuredResponse(reply_text=raw, actions=[])
