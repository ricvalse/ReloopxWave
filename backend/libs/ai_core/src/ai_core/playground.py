"""UC-08 — playground turn runner (dry-run simulation).

The playground is a faithful PREVIEW of a real customer WhatsApp conversation:
same system prompt and settings as the live flow (see ADR 0009), plus a
side-effect-free DRY-RUN of the bot's tools.

For each turn it:
  - resolves the same system prompt (`build_cascade_system_prompt`) using the
    PRIOR turn's sentiment, and runs the same orchestrator + model router;
  - analyses this turn's sentiment (gpt-5-nano) exactly like production;
  - SIMULATES the emitted actions (book_slot / move_pipeline / update_score /
    escalate_human) via `playground_sim` — producing human-readable events and
    an evolving lead state — without any real GHL/DB/WhatsApp side-effects;
  - splits the reply into WhatsApp-style bubbles with per-bubble typing delays
    (reusing `delivery`), so the UI can play it back like a real chat.

Carried state (score, sentiment, captured identity, pipeline stage, booked,
escalated, turn_count) travels with the request/response so a multi-turn
conversation evolves like a real one. The backend stays stateless per request.

Invariant: NOTHING is persisted — no DB writes, no GHL calls, no WhatsApp sends,
no analytics. Only config reads + read-only LLM calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ai_core.conversation_service import _to_chat_history as to_chat_history
from ai_core.conversation_service import build_cascade_system_prompt
from ai_core.delivery import compute_typing_delay_s, split_into_bubbles
from ai_core.orchestrator import ConversationContext, ConversationOrchestrator, OrchestratorResponse
from ai_core.playground_sim import PlaygroundLeadState, simulate_turn
from ai_core.rag import Embedder, RAGEngine
from ai_core.sentiment import SentimentAnalyzer
from config_resolver import ConfigKey, ConfigResolver
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
    history: list[PlaygroundMessage]
    user_message: str
    state: PlaygroundLeadState | None = None


@dataclass(slots=True)
class PlaygroundResponse:
    reply_text: str
    actions: list[dict[str, Any]]
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    retrieved_chunks: list[dict[str, Any]]
    # Dry-run additions:
    bubbles: list[dict[str, Any]] = field(default_factory=list)  # {text, delay_ms}
    typing_indicator: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)


class PlaygroundRunner:
    def __init__(
        self,
        *,
        orchestrator: ConversationOrchestrator,
        embedder: Embedder | None,
        sentiment: SentimentAnalyzer | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._embedder = embedder
        self._sentiment = sentiment

    async def run(self, req: PlaygroundRequest) -> PlaygroundResponse:
        ctx = TenantContext(
            tenant_id=req.tenant_id,
            merchant_id=req.merchant_id,
            role="playground",
            actor_id=req.merchant_id,
        )
        state_in = req.state or PlaygroundLeadState()

        retrieved: list[dict[str, Any]] = []
        async with tenant_session(ctx) as session:
            resolver = ConfigResolver(session)

            async def _int(key: ConfigKey, default: int) -> int:
                try:
                    value = await resolver.resolve(key, merchant_id=req.merchant_id)
                except Exception:
                    return default
                return value if isinstance(value, int) and not isinstance(value, bool) else default

            async def _float(key: ConfigKey, default: float) -> float:
                try:
                    value = await resolver.resolve(key, merchant_id=req.merchant_id)
                except Exception:
                    return default
                if isinstance(value, int | float) and not isinstance(value, bool):
                    return float(value)
                return default

            async def _bool(key: ConfigKey, default: bool) -> bool:
                try:
                    value = await resolver.resolve(key, merchant_id=req.merchant_id)
                except Exception:
                    return default
                return value if isinstance(value, bool) else default

            async def _str(key: ConfigKey) -> str | None:
                try:
                    value = await resolver.resolve(key, merchant_id=req.merchant_id)
                except Exception:
                    return None
                return value.strip() if isinstance(value, str) and value.strip() else None

            # This turn's sentiment (read-only LLM call). The PRIOR turn's
            # sentiment (carried in state) is what adapts the prompt below.
            current_sentiment: str | None = None
            if self._sentiment is not None:
                try:
                    current_sentiment = await self._sentiment.analyze(
                        merchant_id=req.merchant_id,
                        tenant_id=req.tenant_id,
                        text=req.user_message,
                    )
                except Exception as e:
                    logger.warning("uc08.sentiment_failed", error=str(e))

            # RAG retrieval — always on when an embedder is configured.
            kb_chunks = []
            if self._embedder is not None:
                try:
                    top_k = await _int(ConfigKey.RAG_TOP_K, 5)
                    min_score = await _float(ConfigKey.RAG_MIN_SCORE, 0.7)
                    rag = RAGEngine(session, self._embedder)
                    kb_chunks = await rag.retrieve(
                        req.user_message,
                        merchant_id=req.merchant_id,
                        top_k=top_k,
                        min_score=min_score,
                    )
                    retrieved = [
                        {"chunk_id": str(c.chunk_id), "score": c.score, "snippet": c.content[:280]}
                        for c in kb_chunks
                    ]
                except Exception as e:
                    logger.warning("uc08.rag_failed", error=str(e))

            # Exact same system prompt as the live turn, adapted to the prior
            # turn's sentiment (no conversation = no A/B variant).
            system_prompt = await build_cascade_system_prompt(
                session=session,
                merchant_id=req.merchant_id,
                prior_sentiment=state_in.lead_sentiment,
            )

            hot_threshold = await _int(ConfigKey.SCORING_HOT_THRESHOLD, 80)
            cold_threshold = await _int(ConfigKey.SCORING_COLD_THRESHOLD, 30)
            qualified_stage = await _str(ConfigKey.PIPELINE_QUALIFIED_STAGE_ID)

            orchestrator_ctx = ConversationContext(
                merchant_id=req.merchant_id,
                tenant_id=req.tenant_id,
                lead_id=None,
                # Carried score → the hot_lead escalation can fire across turns,
                # exactly as a lead warms up in production.
                lead_score=state_in.lead_score,
                hot_threshold=hot_threshold,
                system_prompt=system_prompt,
                history=to_chat_history(req.history),
                kb_chunks=kb_chunks,
                variant_id=None,
            )

            response: OrchestratorResponse = await self._orchestrator.run(
                orchestrator_ctx, req.user_message
            )

            # Side-effect-free dry-run of the emitted actions.
            sim = simulate_turn(
                actions=response.actions,
                state=state_in,
                current_sentiment=current_sentiment,
                hot_threshold=hot_threshold,
                cold_threshold=cold_threshold,
                qualified_stage_default=qualified_stage,
                history_len=len(req.history),
            )
            sim.state.lead_sentiment = current_sentiment

            # WhatsApp-style delivery: split into bubbles + per-bubble delay.
            multi_bubble_max = await _int(ConfigKey.DELIVERY_MULTI_BUBBLE_MAX, 1)
            bubble_max_chars = await _int(ConfigKey.DELIVERY_BUBBLE_MAX_CHARS, 600)
            typing_indicator = await _bool(ConfigKey.DELIVERY_TYPING_INDICATOR_ENABLED, False)
            delay_base = await _float(ConfigKey.DELIVERY_TYPING_DELAY_BASE_S, 0.0)
            delay_per_char = await _float(ConfigKey.DELIVERY_TYPING_DELAY_PER_CHAR_S, 0.0)
            delay_min = await _float(ConfigKey.DELIVERY_TYPING_DELAY_MIN_S, 0.0)
            delay_max = await _float(ConfigKey.DELIVERY_TYPING_DELAY_MAX_S, 0.0)
            jitter = await _float(ConfigKey.DELIVERY_TYPING_JITTER_FRAC, 0.0)

            texts = split_into_bubbles(
                response.reply_text, max_bubbles=multi_bubble_max, max_chars=bubble_max_chars
            ) or [response.reply_text]
            # Booking confirmation (and any extra) arrive as trailing bubbles,
            # just like the separate WhatsApp message the live booking sends.
            texts.extend(sim.extra_bubbles)
            bubbles = [
                {
                    "text": text,
                    "delay_ms": int(
                        compute_typing_delay_s(
                            text,
                            base_s=delay_base,
                            per_char_s=delay_per_char,
                            min_s=delay_min,
                            max_s=delay_max,
                            jitter_frac=jitter,
                            seed=f"{state_in.turn_count}:{i}",
                        )
                        * 1000
                    ),
                }
                for i, text in enumerate(texts)
            ]

        return PlaygroundResponse(
            reply_text=response.reply_text,
            actions=[{"kind": a.kind, "payload": a.payload} for a in response.actions],
            model=response.model,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            latency_ms=response.latency_ms,
            retrieved_chunks=retrieved,
            bubbles=bubbles,
            typing_indicator=typing_indicator,
            events=[e.to_dict() for e in sim.events],
            state=sim.state.to_dict(),
        )
