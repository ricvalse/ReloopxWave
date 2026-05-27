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


def derive_conversation_signals(
    *,
    has_name: bool,
    has_email: bool,
    turn_count: int,
    sentiment: str | None,
    asked_for_booking: bool,
    llm_signals: dict[str, bool],
) -> dict[str, bool]:
    """Combine content signals reported by the LLM with behavioural signals the
    system derives from cumulative conversation state.

    This is what makes scoring always-on and cumulative (UC-05): the behavioural
    signals (has_name/has_email/engaged/positive_sentiment/asked_for_booking)
    reflect the whole conversation so far, not just the current message, so a
    single late negative turn can no longer crater an otherwise-hot lead — the
    score is recomputed from accumulated facts every turn.

    The LLM contributes only *content* signals (budget, timeline, objections,
    profanity, dropped_off); behavioural ones are owned by the system.
    """
    signals: dict[str, bool] = {k: True for k, v in llm_signals.items() if v}
    if has_name:
        signals["has_name"] = True
    if has_email:
        signals["has_email"] = True
    if turn_count >= 3:
        signals["engaged_multiple_turns"] = True
    if sentiment == "positive":
        signals["positive_sentiment"] = True
    if asked_for_booking:
        signals["asked_for_booking"] = True
    return signals
