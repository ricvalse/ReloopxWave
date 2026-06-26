"""Unit tests for S-09: proactive escalation risk predictor."""

from __future__ import annotations

import pytest

from ai_core.escalation_predictor import EscalationRisk, predict_escalation_risk


def _predict(**overrides) -> EscalationRisk:
    defaults = dict(
        turn_count=3,
        lead_score=20,
        hot_threshold=70,
        sentiment="neutral",
        objection_count=0,
        recent_messages=[],
        avg_response_latency_seconds=None,
    )
    defaults.update(overrides)
    return predict_escalation_risk(**defaults)


class TestPredictEscalationRisk:
    def test_no_signals_low_risk(self):
        risk = _predict()
        assert risk.score < 30
        assert risk.factors == []

    def test_negative_sentiment_raises_score(self):
        risk = _predict(sentiment="negative")
        assert risk.score >= 25
        assert "negative_sentiment" in risk.factors

    def test_high_objection_count_raises_score(self):
        risk = _predict(objection_count=3)
        assert risk.score >= 20
        assert "high_objection_count" in risk.factors

    def test_long_conversation_raises_score(self):
        risk = _predict(turn_count=12)
        assert risk.score >= 15
        assert "long_conversation" in risk.factors

    def test_hot_lead_raises_score(self):
        risk = _predict(lead_score=80, hot_threshold=70)
        assert "hot_lead_at_risk" in risk.factors

    def test_frustration_keywords_raise_score(self):
        risk = _predict(recent_messages=["non funziona, è assurdo!", "voglio il rimborso"])
        assert "frustration_keywords:non funziona" in " ".join(risk.factors) or \
               any("frustration_keywords" in f for f in risk.factors)
        assert risk.score >= 25

    def test_rapid_replies_adds_points(self):
        risk = _predict(avg_response_latency_seconds=10)
        assert "rapid_replies" in risk.factors

    def test_score_capped_at_100(self):
        risk = _predict(
            sentiment="negative",
            objection_count=5,
            turn_count=20,
            lead_score=90,
            hot_threshold=70,
            recent_messages=["non funziona", "assurdo", "rimborso", "avvocato"],
            avg_response_latency_seconds=5,
        )
        assert risk.score == 100

    def test_score_range_always_0_to_100(self):
        for turns in [0, 5, 10, 15, 20]:
            r = _predict(turn_count=turns)
            assert 0 <= r.score <= 100

    def test_no_frustration_keywords_no_factor(self):
        risk = _predict(recent_messages=["ciao come stai", "tutto bene grazie"])
        assert not any("frustration" in f for f in risk.factors)
