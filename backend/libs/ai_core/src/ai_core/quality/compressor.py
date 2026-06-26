"""Context compressor — summarises old conversation turns into a memory block.

When a conversation exceeds COMPRESS_THRESHOLD turns, the oldest turns are
replaced with a compact Italian summary. The summary is persisted in
conversations.context_summary and injected as a synthetic system message.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from shared import get_logger

logger = get_logger(__name__)

DEFAULT_COMPRESS_THRESHOLD = 30
_KEEP_RECENT = 10  # always keep the most recent N turns verbatim


@dataclass(slots=True, frozen=True)
class MemoryBlock:
    text: str
    compressed_turns: int
    compressed_at: str  # ISO timestamp


def memory_block_as_message(block: MemoryBlock) -> Any:
    """Return a ChatMessage-compatible dict with role='system'."""
    from ai_core.llm import ChatMessage

    return ChatMessage(
        role="system",
        content=(
            f"[RIEPILOGO CONVERSAZIONE PRECEDENTE — {block.compressed_turns} turni compressi]\n"
            f"{block.text}"
        ),
    )


class ContextCompressor:
    def __init__(self, llm_client: Any) -> None:
        self._llm = llm_client

    async def compress(self, messages: list[Any]) -> MemoryBlock | None:
        """Summarise messages[:-KEEP_RECENT] into a MemoryBlock. Returns None on error."""
        older = messages[:-_KEEP_RECENT]
        if not older:
            return None
        try:
            from ai_core.llm import ChatMessage

            history_text = "\n".join(
                f"[{m.role}]: {m.content[:300]}" for m in older
            )
            prompt = (
                "Riassumi questa conversazione estraendo SOLO i fatti chiave:\n"
                "- Nome e dati di contatto del lead\n"
                "- Prodotto/servizio di interesse\n"
                "- Budget o tempistiche menzionati\n"
                "- Obiezioni sollevate\n"
                "- Accordi o impegni già presi\n\n"
                f"Conversazione:\n{history_text}\n\n"
                "Rispondi in italiano, max 200 parole, senza lista puntata."
            )
            resp = await self._llm.complete(
                messages=[ChatMessage(role="user", content=prompt)],
                max_tokens=300,
            )
            summary = getattr(resp, "content", "").strip()
            if not summary:
                return None
            return MemoryBlock(
                text=summary,
                compressed_turns=len(older),
                compressed_at=datetime.now(UTC).isoformat(),
            )
        except Exception as e:
            logger.debug("compressor.compress_failed", error=str(e))
            return None
