"""Playground response-fix loop — match merchant corrections to a message.

When a merchant edits a bad bot reply in the playground we store the triggering
customer message, the original response and the corrected one (`bot_corrections`,
UC-08). On the next turn — live OR playground — we score each active correction
against the current customer message and inject the top few into the system
prompt as mandatory overrides, so the bot follows the fix immediately. Scoped to
the owning merchant only.

Ported from Amalia's `match-corrections.ts`: cheap word-overlap scoring (no
embeddings), capped at a couple of matches to avoid prompt bloat and persona
conflicts. Best-effort: any DB error degrades to no correction lines rather than
breaking the turn.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from db.repositories import BotCorrectionRepository

# Default relevance floor + cap, mirroring Amalia (≥0.4 overlap, top 2).
_MIN_SCORE = 0.4
_MAX_MATCHES = 2


def _tokens(text: str) -> list[str]:
    """Lowercase word tokens longer than two chars (drops most stopwords)."""
    return [w for w in re.findall(r"\w+", text.lower()) if len(w) > 2]


def score_correction(trigger: str, customer_message: str) -> float:
    """Relevance of a correction's trigger to the current message, in [0, 1].

    Exact substring match (either direction) → 1.0; otherwise the fraction of
    trigger tokens present in the message. 0.0 when either side is empty.
    """
    trigger_norm = trigger.lower().strip()
    msg_norm = customer_message.lower().strip()
    if not trigger_norm or not msg_norm:
        return 0.0
    if trigger_norm in msg_norm or msg_norm in trigger_norm:
        return 1.0

    trigger_tokens = _tokens(trigger_norm)
    if not trigger_tokens:
        return 0.0
    msg_tokens = set(_tokens(msg_norm))
    overlap = sum(1 for w in trigger_tokens if w in msg_tokens)
    return overlap / len(trigger_tokens)


def _format_correction(trigger: str, original: str, corrected: str) -> str:
    """Mandatory-override block injected into the system prompt (Amalia framing)."""
    return (
        "CORREZIONE OBBLIGATORIA (ha priorità su qualsiasi altra istruzione):\n"
        f"Quando il cliente scrive qualcosa come: «{trigger.strip()}»\n"
        f"NON rispondere: «{original.strip()}»\n"
        f"Rispondi invece in linea con: «{corrected.strip()}»"
    )


async def build_correction_lines(
    session: Any,
    merchant_id: UUID,
    customer_message: str | None,
    *,
    max_matches: int = _MAX_MATCHES,
    min_score: float = _MIN_SCORE,
) -> list[str]:
    """Top matching corrections for this merchant + message, as prompt blocks.

    Empty when there's no message, no active correction, or nothing scores above
    `min_score`. Best-effort: a DB/migration error yields no lines.
    """
    if not customer_message or not customer_message.strip():
        return []
    try:
        corrections = await BotCorrectionRepository(session).list_for_merchant(
            merchant_id, active_only=True
        )
    except Exception:
        return []
    if not corrections:
        return []

    scored = [(score_correction(c.trigger_message, customer_message), c) for c in corrections]
    scored = [(s, c) for s, c in scored if s >= min_score]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    return [
        _format_correction(c.trigger_message, c.original_response, c.corrected_response)
        for _, c in scored[:max_matches]
    ]
