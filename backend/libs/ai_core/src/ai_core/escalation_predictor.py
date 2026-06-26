"""S-09 — Proactive escalation risk predictor.

Estimates the probability (0-100) that the current conversation will require
human escalation, BEFORE the hard escalation triggers in router.py fire.

Running this at each turn lets the orchestrator:
  - Inject a softer/more empathetic tone hint into the system prompt,
  - Emit an analytics event when risk crosses a threshold (alerting the dashboard),
  - Ultimately pre-empt the `escalate_human` action by resolving issues early.

All inputs are integers/booleans derived from already-computed context so this
is a cheap heuristic pass — no LLM call, no DB query, runs in < 1ms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# Keyword fragments that indicate frustration or urgency in Italian WhatsApp context.
_FRUSTRATION_KEYWORDS = frozenset(
    [
        "non funziona",
        "deluso",
        "delusa",
        "inaccettabile",
        "vergogna",
        "truffato",
        "truffata",
        "avvocato",
        "denuncia",
        "assurdo",
        "insopportabile",
        "non ci credo",
        "ridicolo",
        "basta",
        "abbandono",
        "cancello",
        "rimbors",
        "rimborso",
        "soldi indietro",
        "non tornerò",
    ]
)


@dataclass(slots=True, frozen=True)
class EscalationRisk:
    score: int  # 0-100
    factors: list[str]  # human-readable reasons


def predict_escalation_risk(
    *,
    turn_count: int,
    lead_score: int,
    hot_threshold: int,
    sentiment: str | None,
    objection_count: int,
    recent_messages: Sequence[str],
    avg_response_latency_seconds: int | None,
) -> EscalationRisk:
    """Compute a heuristic escalation risk score.

    Args:
        turn_count: number of turns so far (each = user + assistant).
        lead_score: current numeric lead score (0-100).
        hot_threshold: threshold above which a lead is "hot".
        sentiment: latest inferred sentiment label ("positive"/"neutral"/"negative"/None).
        objection_count: how many objections have been extracted for this conversation.
        recent_messages: last 3-5 user message texts for keyword scan.
        avg_response_latency_seconds: average latency of lead replies (lower = more engaged).

    Returns:
        EscalationRisk with score 0-100 and explanatory factors.
    """
    score = 0
    factors: list[str] = []

    # Sentiment signal: negative → +25
    if sentiment == "negative":
        score += 25
        factors.append("negative_sentiment")

    # Many objections in this conversation → +20
    if objection_count >= 3:
        score += 20
        factors.append("high_objection_count")
    elif objection_count >= 1:
        score += 10

    # Long conversation without closing → +15
    if turn_count >= 12:
        score += 15
        factors.append("long_conversation")
    elif turn_count >= 8:
        score += 8

    # Hot lead not yet booked → +10 (high stakes, don't lose them)
    if lead_score >= hot_threshold:
        score += 10
        factors.append("hot_lead_at_risk")

    # Frustration keywords in recent messages → +25
    low_msgs = " ".join(m.lower() for m in recent_messages)
    matched_kws = [kw for kw in _FRUSTRATION_KEYWORDS if kw in low_msgs]
    if matched_kws:
        score += min(25, 10 + len(matched_kws) * 5)
        factors.append(f"frustration_keywords:{','.join(matched_kws[:3])}")

    # Very fast responses can indicate impatience/urgency → +5
    if avg_response_latency_seconds is not None and avg_response_latency_seconds < 30:
        score += 5
        factors.append("rapid_replies")

    return EscalationRisk(score=min(100, score), factors=factors)
