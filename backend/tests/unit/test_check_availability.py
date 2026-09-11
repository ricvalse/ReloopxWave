"""`check_availability` tool — `_availability_summary` (unit, pure function).

The one property that matters here: spreading candidates out for the DISPLAYED
list must never affect whether the exact requested slot is reported as free.
The AI states this summary as fact to the customer, so a false "not free"
caused only by a display-thinning pass would be a regression, not a cosmetic
issue.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from ai_core.actions.read_tools import _availability_summary

_TZ = ZoneInfo("Europe/Rome")


def test_no_preferred_slot_lists_spread_out_alternatives() -> None:
    """Raw GHL granularity (10:00, 10:05, 10:10 — a few minutes apart) must not
    read as the same instant offered three times."""
    free = [
        "2026-04-25T10:00:00+02:00",
        "2026-04-25T10:05:00+02:00",
        "2026-04-25T10:10:00+02:00",
        "2026-04-25T11:00:00+02:00",
    ]

    summary = _availability_summary(free, None, _TZ, min_gap_minutes=30)

    assert "10:00" in summary
    assert "11:00" in summary
    assert "10:05" not in summary
    assert "10:10" not in summary


def test_preferred_slot_membership_is_not_affected_by_spreading() -> None:
    """The requested slot (10:05) would be DROPPED by a naive spread pass
    (it's within 30 min of 10:00), but the free/not-free verdict must still be
    correct — spreading is a display concern for the *listing*, never for the
    "is this exact slot free" check."""
    free = [
        "2026-04-25T10:00:00+02:00",
        "2026-04-25T10:05:00+02:00",
        "2026-04-25T10:10:00+02:00",
    ]

    summary = _availability_summary(free, "2026-04-25T10:05:00+02:00", _TZ, min_gap_minutes=30)

    assert "LIBERO" in summary


def test_no_free_slots_at_all() -> None:
    assert _availability_summary([], None, _TZ) == "Nessuno slot libero nel periodo richiesto."


def test_requested_slot_not_free_lists_spread_alternatives() -> None:
    free = [
        "2026-04-25T09:00:00+02:00",
        "2026-04-25T09:05:00+02:00",
        "2026-04-25T11:00:00+02:00",
    ]

    summary = _availability_summary(free, "2026-04-25T14:00:00+02:00", _TZ, min_gap_minutes=30)

    assert "NON è libero" in summary
    assert "09:00" in summary
    assert "11:00" in summary
    assert "09:05" not in summary
