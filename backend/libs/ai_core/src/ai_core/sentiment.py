"""Sentiment analyzer — lightweight per-turn classification (spec 6.6).

Routes through the ModelRouter `purpose="sentiment"` branch (gpt-5-nano), so it
is cheap to run on every inbound turn. The label is persisted on `lead.sentiment`
and fed into lead scoring (UC-05, `positive_sentiment` signal) and pipeline
notes (UC-04). Degrades to "neutral" on any failure — sentiment must never
block a reply.
"""

from __future__ import annotations

from uuid import UUID

from ai_core.llm import ChatMessage
from ai_core.router import ModelRouter, RoutingRequest
from shared import get_logger

logger = get_logger(__name__)

LABELS = ("positive", "neutral", "negative")

_PROMPT = (
    "Classifica il sentiment del messaggio del cliente verso l'azienda. "
    "Rispondi con UNA sola parola, esattamente una tra: positive, neutral, "
    "negative. Nessun'altra parola, nessuna punteggiatura."
)


class SentimentAnalyzer:
    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def analyze(self, *, merchant_id: UUID, tenant_id: UUID, text: str) -> str:
        if not text or not text.strip():
            return "neutral"
        req = RoutingRequest(
            merchant_id=merchant_id,
            tenant_id=tenant_id,
            context_tokens=len(text) // 4,
            turn_count=0,
            lead_score=0,
            hot_threshold=80,
            escalate_keywords_matched=False,
            purpose="sentiment",
        )
        client = await self._router.select(req)
        try:
            result = await client.complete(
                messages=[
                    ChatMessage(role="system", content=_PROMPT),
                    ChatMessage(role="user", content=text),
                ]
            )
        except Exception as e:
            logger.warning("sentiment.failed", error=str(e), merchant_id=str(merchant_id))
            return "neutral"
        label = (result.content or "").strip().lower()
        # Tolerate stray words/punctuation the model may add.
        for candidate in LABELS:
            if candidate in label:
                return candidate
        return "neutral"
