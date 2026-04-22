"""UC-05 Lead scoring — rules-based for V1 (section 6.4).

Output is stable and auditable: (score, reason_codes). Hooks in V2 can swap
the function body for an ML model without changing the call site.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class LeadScore:
    score: int
    reason_codes: list[str]


SIGNAL_WEIGHTS = {
    "has_name": 5,
    "has_email": 5,
    "has_budget": 20,
    "has_timeline": 15,
    "positive_sentiment": 10,
    "engaged_multiple_turns": 15,
    "responded_within_10min": 10,
    "asked_for_booking": 20,
    "objection_price": -15,
    "objection_trust": -10,
    "objection_competitor": -5,
    "dropped_off": -20,
    "profanity": -30,
}


def score_lead(signals: dict[str, Any]) -> LeadScore:
    score = 0
    reasons: list[str] = []
    for signal, weight in SIGNAL_WEIGHTS.items():
        if signals.get(signal):
            score += weight
            reasons.append(signal)

    score = max(0, min(100, score))
    return LeadScore(score=score, reason_codes=reasons)
