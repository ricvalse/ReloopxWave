from ai_core.scoring import score_lead


def test_empty_signals_zero_score() -> None:
    assert score_lead({}).score == 0


def test_positive_signals_sum_clamped_at_100() -> None:
    signals = {k: True for k in [
        "has_name", "has_email", "has_budget", "has_timeline",
        "positive_sentiment", "engaged_multiple_turns", "responded_within_10min",
        "asked_for_booking",
    ]}
    assert score_lead(signals).score == 100


def test_negative_signals_clamp_at_zero() -> None:
    out = score_lead({"dropped_off": True, "profanity": True})
    assert out.score == 0
    assert set(out.reason_codes) == {"dropped_off", "profanity"}
