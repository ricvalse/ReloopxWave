"""Pure tests for the typing-delay computation."""

from __future__ import annotations

from ai_core.delivery import compute_typing_delay_s


def _delay(text: str, **kw) -> float:
    base = {"base_s": 0.5, "per_char_s": 0.05, "min_s": 1.0, "max_s": 6.0}
    base.update(kw)
    return compute_typing_delay_s(text, **base)


def test_disabled_when_max_zero() -> None:
    assert compute_typing_delay_s("anything", base_s=1, per_char_s=1, min_s=1, max_s=0) == 0.0


def test_monotonic_in_length() -> None:
    short = _delay("ciao")
    long = _delay("ciao " * 20)
    assert long >= short


def test_clamped_to_min_and_max() -> None:
    # Tiny text → floored at min.
    assert _delay("a") == 1.0
    # Huge text → capped at max.
    assert _delay("x" * 1000) == 6.0


def test_jitter_is_bounded_and_deterministic() -> None:
    kw = {"base_s": 2.0, "per_char_s": 0.0, "min_s": 0.0, "max_s": 6.0, "jitter_frac": 0.25}
    a = compute_typing_delay_s("hello", seed="seed-1", **kw)
    b = compute_typing_delay_s("hello", seed="seed-1", **kw)
    assert a == b  # deterministic for a fixed seed
    # Within +/- 25% of the raw 2.0s (and inside [0, max]).
    assert 1.5 <= a <= 2.5


def test_jitter_varies_with_seed() -> None:
    kw = {"base_s": 2.0, "per_char_s": 0.0, "min_s": 0.0, "max_s": 6.0, "jitter_frac": 0.5}
    a = compute_typing_delay_s("hello", seed="seed-a", **kw)
    b = compute_typing_delay_s("hello", seed="seed-b", **kw)
    # Different seeds should (almost surely) give different nudges.
    assert a != b
