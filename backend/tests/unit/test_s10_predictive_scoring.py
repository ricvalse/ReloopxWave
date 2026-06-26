"""Unit tests for S-10: predictive booking-probability scoring."""

from __future__ import annotations

import pytest

from ai_core.predictive_scoring import PredictiveScore, compute_booking_probability


def _score(**overrides) -> PredictiveScore:
    defaults = dict(
        content_signals={},
        effective_score=None,
        sentiment=None,
        avg_response_latency_seconds=None,
        intake_score=None,
        turn_count=1,
        was_read=None,
        velocity_flag=None,
    )
    defaults.update(overrides)
    return compute_booking_probability(**defaults)


class TestComputeBookingProbability:
    def test_no_signals_baseline_score(self):
        ps = _score()
        # neutral priors → ~50% of the weighted sum
        assert 0 <= ps.probability <= 100

    def test_high_signals_high_probability(self):
        ps = _score(
            content_signals={"has_budget": True, "has_timeline": True, "has_name": True,
                              "has_email": True, "interested": True},
            effective_score=80.0,
            sentiment="positive",
            avg_response_latency_seconds=30,
            intake_score=80,
            turn_count=8,
            was_read=0.9,
            velocity_flag="high",
        )
        assert ps.probability >= 70

    def test_negative_signals_low_probability(self):
        ps = _score(
            content_signals={},
            effective_score=5.0,
            sentiment="negative",
            avg_response_latency_seconds=3600,
            intake_score=5,
            turn_count=1,
            was_read=0.1,
            velocity_flag="stalled",
        )
        assert ps.probability <= 40

    def test_probability_range_0_to_100(self):
        for combo in [
            {},
            {"effective_score": 100, "sentiment": "positive"},
            {"effective_score": 0, "sentiment": "negative"},
            {"content_signals": {f"sig_{i}": True for i in range(10)}},
        ]:
            ps = _score(**combo)
            assert 0 <= ps.probability <= 100, f"Out of range: {ps.probability} for {combo}"

    def test_dominant_feature_is_valid(self):
        ps = _score(effective_score=90.0)
        valid_features = {
            "effective_score", "content_signals", "sentiment",
            "response_speed", "intake_score", "engagement_depth",
            "read_ratio", "velocity",
        }
        assert ps.dominant_feature in valid_features

    def test_content_signals_boost_score(self):
        baseline = _score()
        boosted = _score(
            content_signals={"has_budget": True, "has_timeline": True, "interested": True}
        )
        assert boosted.probability > baseline.probability

    def test_fast_response_boosts_score(self):
        slow = _score(avg_response_latency_seconds=3600)
        fast = _score(avg_response_latency_seconds=10)
        assert fast.probability > slow.probability

    def test_high_velocity_boosts_score(self):
        stalled = _score(velocity_flag="stalled")
        high = _score(velocity_flag="high")
        assert high.probability > stalled.probability

    def test_features_dict_present(self):
        ps = _score(effective_score=50.0)
        assert isinstance(ps.features, dict)
        assert "effective_score" in ps.features
        assert 0.0 <= ps.features["effective_score"] <= 1.0
