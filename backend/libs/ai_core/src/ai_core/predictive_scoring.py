"""S-10 — Predictive lead scoring.

Estimates the probability (0-100) that a lead will book an appointment,
based on the behavioral and content signals accumulated across their
interactions. This complements the reactive scoring (UC-05) with a
forward-looking estimate useful for:

  - Dashboard "hot leads to call back" prioritisation,
  - Scheduler deciding whether to send a high-value reactivation message,
  - Exportable analytics for agency insights.

The model is a logistic-regression-inspired weighted sum with calibrated
default weights. No ML training is needed — the features are designed to
be meaningful by construction and the weights can be tuned per merchant
via ConfigKey.PREDICTIVE_SCORE_WEIGHTS (future).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class PredictiveScore:
    probability: int  # 0-100
    dominant_feature: str  # most impactful feature for the score
    features: dict[str, float]  # raw feature values


def compute_booking_probability(
    *,
    content_signals: dict[str, bool],
    effective_score: float | None,
    sentiment: str | None,
    avg_response_latency_seconds: int | None,
    intake_score: int | None,
    turn_count: int,
    was_read: float | None,
    velocity_flag: str | None,
) -> PredictiveScore:
    """Compute the probability (0-100) that this lead will book.

    Args:
        content_signals: dict from lead.meta["content_signals"], e.g.
                         {"has_budget": True, "has_timeline": True, ...}.
        effective_score: score after temporal decay (lead.effective_score).
        sentiment: "positive" / "neutral" / "negative".
        avg_response_latency_seconds: EMA response latency.
        intake_score: intent score computed on first message.
        turn_count: total conversation turns.
        was_read: read-receipt ratio (0.0-1.0).
        velocity_flag: "high" / "normal" / "stalled".

    Returns:
        PredictiveScore with probability and feature breakdown.
    """
    feats: dict[str, float] = {}

    # Feature 1: effective (decayed) lead score — already normalised 0-100
    feats["effective_score"] = float(effective_score or 0) / 100.0

    # Feature 2: content signal count (has_budget, has_timeline, has_name, ...)
    n_signals = sum(1 for v in content_signals.values() if v)
    feats["content_signals"] = min(1.0, n_signals / 5.0)  # saturate at 5 signals

    # Feature 3: sentiment
    sentiment_map = {"positive": 1.0, "neutral": 0.5, "negative": 0.0}
    feats["sentiment"] = sentiment_map.get(sentiment or "neutral", 0.5)

    # Feature 4: response speed — fast replies = engaged
    if avg_response_latency_seconds is not None:
        feats["response_speed"] = 1.0 / (1.0 + avg_response_latency_seconds / 300.0)
    else:
        feats["response_speed"] = 0.5  # neutral prior

    # Feature 5: intake score on first message
    feats["intake_score"] = float(intake_score or 0) / 100.0

    # Feature 6: engagement depth (more turns = more invested)
    feats["engagement_depth"] = min(1.0, turn_count / 10.0)

    # Feature 7: read receipt ratio
    feats["read_ratio"] = float(was_read or 0.5)

    # Feature 8: velocity ("high" velocity = accelerating engagement)
    velocity_map = {"high": 1.0, "normal": 0.5, "stalled": 0.0}
    feats["velocity"] = velocity_map.get(velocity_flag or "normal", 0.5)

    # Weights (logistic-regression-inspired, sum = 1.0)
    WEIGHTS = {
        "effective_score": 0.25,
        "content_signals": 0.20,
        "sentiment": 0.15,
        "response_speed": 0.10,
        "intake_score": 0.10,
        "engagement_depth": 0.08,
        "read_ratio": 0.07,
        "velocity": 0.05,
    }

    raw = sum(WEIGHTS[k] * feats[k] for k in WEIGHTS)
    probability = round(raw * 100)

    dominant = max(WEIGHTS, key=lambda k: WEIGHTS[k] * feats[k])

    return PredictiveScore(
        probability=min(100, max(0, probability)),
        dominant_feature=dominant,
        features=feats,
    )
