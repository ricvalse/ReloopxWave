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


def test_italian_mobile_national_format_gets_country_code_repaired() -> None:
    # GHL 2026-09-16 (ADR 0035): a contact phone typed without "+39" produced a
    # digit string that never matched the same lead's WhatsApp "from" — two
    # Lead/Conversation rows for one person, the automation's touch on one
    # thread and the reply on the other. An Italian mobile in national format
    # (10 digits, starts with "3") is unambiguous enough to repair.
    assert normalize_phone("333 000 0000") == "393330000000"
    assert normalize_phone("3330000000") == "393330000000"


def test_non_italian_local_format_still_passes_through_unrepaired() -> None:
    # No per-merchant country default in V1: only the unambiguous Italian
    # mobile shape (10 digits starting with "3") is repaired. Anything else
    # without a recognisable prefix — a landline, a shorter/longer local
    # number, a foreign mobile — can't be guessed and passes through as-is.
    assert normalize_phone("06 1234 5678") == "0612345678"  # IT landline, 10 digits, not "3xxx"
    assert normalize_phone("41791234567") == "41791234567"  # already has a (non-IT) country code
