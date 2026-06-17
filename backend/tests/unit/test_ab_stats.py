"""UC-09 — two-proportion significance test (pure)."""

from __future__ import annotations

from ai_core.ab_stats import evaluate_significance, two_proportion_ztest


def test_ztest_detects_clear_difference() -> None:
    # 50% vs 20% over 100 each → very unlikely to be noise.
    p = two_proportion_ztest(50, 100, 20, 100)
    assert p is not None and p < 0.001


def test_ztest_identical_rates_not_significant() -> None:
    p = two_proportion_ztest(30, 100, 30, 100)
    assert p is not None and p > 0.5


def test_ztest_empty_or_degenerate_returns_none() -> None:
    assert two_proportion_ztest(0, 0, 5, 10) is None
    assert two_proportion_ztest(0, 100, 0, 100) is None  # pooled rate 0
    assert two_proportion_ztest(100, 100, 100, 100) is None  # pooled rate 1


def test_evaluate_picks_significant_winner() -> None:
    res = evaluate_significance([("a", 50, 100), ("b", 20, 100)])
    assert res.significant is True
    assert res.winner == "a"
    assert res.p_value is not None and res.p_value < 0.05
    assert 0.0 <= res.confidence <= 1.0


def test_evaluate_no_winner_when_close() -> None:
    res = evaluate_significance([("a", 11, 100), ("b", 10, 100)])
    assert res.significant is False
    assert res.winner is None


def test_evaluate_needs_two_arms() -> None:
    res = evaluate_significance([("a", 5, 100)])
    assert res.winner is None
    assert res.significant is False
    assert res.confidence == 0.0
