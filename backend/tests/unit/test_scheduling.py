"""CC-CONFIG — active-hours window (pure)."""

from __future__ import annotations

from datetime import UTC, datetime

from ai_core.scheduling import is_within_active_hours

ROME = "Europe/Rome"
# January → Europe/Rome is UTC+1, so these UTC instants map to Rome wall-clock:
#   11:00Z → 12:00,  19:00Z → 20:00,  22:00Z → 23:00.
NOON = datetime(2026, 1, 15, 11, 0, tzinfo=UTC)
EVENING = datetime(2026, 1, 15, 19, 0, tzinfo=UTC)
LATE = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)


def test_always_on_variants() -> None:
    for spec in ("24/7", "", "always", None):
        assert is_within_active_hours(spec, ROME, EVENING) is True


def test_daytime_window() -> None:
    assert is_within_active_hours("09:00-18:00", ROME, NOON) is True
    assert is_within_active_hours("09:00-18:00", ROME, EVENING) is False


def test_overnight_window() -> None:
    assert is_within_active_hours("22:00-06:00", ROME, LATE) is True
    assert is_within_active_hours("22:00-06:00", ROME, NOON) is False


def test_unparseable_fails_open() -> None:
    assert is_within_active_hours("nonsense", ROME, EVENING) is True
    assert is_within_active_hours("9-18", ROME, EVENING) is True  # missing minutes


def test_bad_timezone_falls_back_to_rome() -> None:
    # An invalid tz must not raise; falls back to Europe/Rome.
    assert is_within_active_hours("09:00-18:00", "Not/AZone", NOON) is True
