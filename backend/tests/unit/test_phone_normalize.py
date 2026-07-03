"""`shared.normalize_phone` — the linchpin that lets a GHL E.164 contact match
the digits-only identity WhatsApp leads are keyed on (ADR 0016)."""

from __future__ import annotations

from shared import normalize_phone


def test_e164_reduces_to_digits() -> None:
    assert normalize_phone("+39 333 000-0000") == "393330000000"
    assert normalize_phone("+393330000000") == "393330000000"


def test_digits_only_passes_through() -> None:
    assert normalize_phone("393330000000") == "393330000000"


def test_international_00_prefix_is_dropped() -> None:
    assert normalize_phone("00393330000000") == "393330000000"


def test_garbage_and_short_values_are_rejected() -> None:
    assert normalize_phone(None) is None
    assert normalize_phone("") is None
    assert normalize_phone("n/a") is None
    assert normalize_phone("+39") is None


def test_local_format_passes_through_unrepaired() -> None:
    # No per-merchant country default in V1: a number without country prefix
    # can't be repaired, only normalised to digits.
    assert normalize_phone("333 000 0000") == "3330000000"
