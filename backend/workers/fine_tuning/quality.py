"""Quality filter (FT pipeline step, spec 5.4) — drops low-quality training data.

Runs between collect and export. The collector's outcome filter (booked/
qualified + closed) is a coarse floor; this step removes pairs/conversations
that would teach the model bad habits:
  - conversations where the bot emitted an error/fallback message,
  - premature drop-offs (too few exchanged turns to be informative),
  - empty/degenerate turns,
  - (S-07) near-duplicate pairs (same user message fingerprint seen before),
  - (S-07) degenerate length-ratio pairs (assistant too short or too long).

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

# S-07: length-ratio thresholds — assistant reply must be between 10% and 20x
# the user message length (chars). Outside this range → degenerate pair.
_MIN_LENGTH_RATIO = 0.10
_MAX_LENGTH_RATIO = 20.0

# S-07: near-duplicate fingerprint uses the first 60 chars of user + first 40
# of assistant, lowercased. Keeps one exemplar per cluster.
_DEDUP_USER_PREFIX = 60
_DEDUP_ASST_PREFIX = 40


@dataclass(slots=True)
class QualityReport:
    kept: list[TrainingPair]
    dropped: int = 0
    reasons: dict[str, int] = field(default_factory=dict)


def _looks_like_bot_error(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _FALLBACK_MARKERS)


def _length_ratio(user: str, assistant: str) -> float:
    """assistant_len / user_len; returns inf when user is empty."""
    u = len(user.strip())
    a = len(assistant.strip())
    return a / u if u > 0 else float("inf")


def _dedup_key(pair: TrainingPair) -> str:
    return (
        pair.user.lower()[:_DEDUP_USER_PREFIX]
        + "|"
        + pair.assistant.lower()[:_DEDUP_ASST_PREFIX]
    )


def score_pair(pair: TrainingPair) -> float:
    """Heuristic quality score (0–1) for a single training pair.

    Higher = more informative example. Used by the export step to rank
    examples and trim the dataset to a budget when needed.
    """
    a_len = len(pair.assistant.strip())
    u_len = len(pair.user.strip())
    if u_len == 0:
        return 0.0
    ratio = a_len / u_len
    # Ideal assistant reply is 1–5× the user length.
    ratio_score = 1.0 if 1.0 <= ratio <= 5.0 else max(0.0, 1.0 - abs(ratio - 3.0) / 10.0)
    # Length score: prefer replies in the 30–300 char range.
    length_score = min(1.0, a_len / 300.0) if a_len < 300 else max(0.5, 1.0 - (a_len - 300) / 2000.0)
    return round((ratio_score + length_score) / 2.0, 4)


def filter_pairs(
    pairs: list[TrainingPair],
    *,
    min_pairs_per_conversation: int = 2,
    deduplicate: bool = True,
) -> QualityReport:
    by_conv: dict[UUID, list[TrainingPair]] = defaultdict(list)
    for p in pairs:
        by_conv[p.conversation_id].append(p)

    report = QualityReport(kept=[])

    def _drop(reason: str, n: int) -> None:
        report.reasons[reason] = report.reasons.get(reason, 0) + n
        report.dropped += n

    seen_keys: set[str] = set()  # S-07: cross-conversation dedup

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

            # S-07: length-ratio filter.
            ratio = _length_ratio(p.user, p.assistant)
            if ratio < _MIN_LENGTH_RATIO or ratio > _MAX_LENGTH_RATIO:
                _drop("bad_length_ratio", 1)
                continue

            # S-07: near-duplicate dedup.
            if deduplicate:
                key = _dedup_key(p)
                if key in seen_keys:
                    _drop("near_duplicate", 1)
                    continue
                seen_keys.add(key)

            report.kept.append(p)

    return report
