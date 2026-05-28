"""Quality filter (FT pipeline step, spec 5.4) — drops low-quality training data.

Runs between collect and export. The collector's outcome filter (booked/
qualified + closed) is a coarse floor; this step removes pairs/conversations
that would teach the model bad habits:
  - conversations where the bot emitted an error/fallback message,
  - premature drop-offs (too few exchanged turns to be informative),
  - empty/degenerate turns.

Returns the kept pairs plus a per-reason drop report for the FT audit trail.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from workers.fine_tuning.collect import TrainingPair

# Substrings that mark a bot error/fallback reply — a conversation containing
# any of these is excluded wholesale (we don't want to train on our own errors).
_FALLBACK_MARKERS = (
    "si è verificato un errore",
    "errore tecnico",
    "non sono riuscito a",
    "riprova più tardi",
    "non ho capito",
    "ti faccio ricontattare",  # graceful LLM-failure fallback message
)

_MIN_USER_CHARS = 2
_MIN_ASSISTANT_CHARS = 5


@dataclass(slots=True)
class QualityReport:
    kept: list[TrainingPair]
    dropped: int = 0
    reasons: dict[str, int] = field(default_factory=dict)


def _looks_like_bot_error(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _FALLBACK_MARKERS)


def filter_pairs(
    pairs: list[TrainingPair], *, min_pairs_per_conversation: int = 2
) -> QualityReport:
    by_conv: dict[UUID, list[TrainingPair]] = defaultdict(list)
    for p in pairs:
        by_conv[p.conversation_id].append(p)

    report = QualityReport(kept=[])

    def _drop(reason: str, n: int) -> None:
        report.reasons[reason] = report.reasons.get(reason, 0) + n
        report.dropped += n

    for conv_pairs in by_conv.values():
        # Conversation-level gates.
        if len(conv_pairs) < min_pairs_per_conversation:
            _drop("premature_dropoff", len(conv_pairs))
            continue
        if any(_looks_like_bot_error(p.assistant) for p in conv_pairs):
            _drop("bot_error", len(conv_pairs))
            continue

        # Turn-level gates.
        for p in conv_pairs:
            if (
                len(p.user.strip()) < _MIN_USER_CHARS
                or len(p.assistant.strip()) < _MIN_ASSISTANT_CHARS
            ):
                _drop("empty_turn", 1)
                continue
            report.kept.append(p)

    return report
