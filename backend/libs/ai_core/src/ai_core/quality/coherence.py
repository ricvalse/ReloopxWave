"""Coherence guard — checks that the AI's proposed reply is consistent with prior turns.

A cheap gpt-4.1-nano call (~200 tokens) verifies the reply doesn't contradict
established facts (names, agreements, product details). On failure the caller
can trigger a retry. Fails open on timeout or LLM error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared import get_logger

logger = get_logger(__name__)

_MAX_HISTORY_TURNS = 10


@dataclass(slots=True, frozen=True)
class CoherenceResult:
    coherent: bool
    issue: str | None


class CoherenceGuard:
    def __init__(self, llm_client: Any) -> None:
        self._llm = llm_client

    async def check(
        self,
        history: list[Any],
        proposed_reply: str,
    ) -> CoherenceResult:
        """Return CoherenceResult. Fails open (coherent=True) on any error."""
        if not history or not proposed_reply.strip():
            return CoherenceResult(coherent=True, issue=None)
        try:
            from ai_core.llm import ChatMessage

            recent = history[-_MAX_HISTORY_TURNS:]
            history_text = "\n".join(
                f"[{m.role}]: {m.content[:200]}" for m in recent
            )
            prompt = (
                f"Conversazione precedente:\n{history_text}\n\n"
                f"Risposta proposta dall'AI: {proposed_reply[:500]}\n\n"
                "La risposta proposta contraddice fatti stabiliti nella conversazione "
                "(nome del cliente, accordi presi, disponibilità confermata, ecc.)? "
                'Rispondi SOLO con JSON: {"coherent": true/false, "issue": "breve spiegazione o null"}'
            )
            resp = await self._llm.complete(
                messages=[ChatMessage(role="user", content=prompt)],
                response_format={"type": "json_object"},
                max_tokens=80,
            )
            import json

            raw = getattr(resp, "content", "{}")
            parsed = json.loads(raw)
            coherent = bool(parsed.get("coherent", True))
            issue = parsed.get("issue") if not coherent else None
            return CoherenceResult(coherent=coherent, issue=issue)
        except Exception as e:
            logger.debug("coherence.check_failed", error=str(e))
            return CoherenceResult(coherent=True, issue=None)
