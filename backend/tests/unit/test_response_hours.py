"""Risoluzione degli orari di risposta: cascata di config + tabella aperture.

Il punto centrale di questi test è il **fail-open**: qualunque cosa vada storta
qui deve produrre "sempre aperto". Il modo di sbagliare che conta davvero non è
un bot che risponde di notte, è un bot che non risponde mai e di cui nessuno si
accorge finché non arriva il reclamo.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from ai_core import response_hours as rh
from config_resolver.schema import ConfigKey

MERCHANT = uuid.uuid4()

# Giovedì 15 gennaio 2026. Roma è UTC+1: 11:00Z = 12:00 locali, 21:00Z = 22:00.
NOON = dt.datetime(2026, 1, 15, 11, 0, tzinfo=dt.UTC)
NIGHT = dt.datetime(2026, 1, 15, 21, 0, tzinfo=dt.UTC)

_WEEKLY_9_18 = [
    {"day": d, "enabled": d <= 4, "windows": [{"start": "09:00", "end": "18:00"}]} for d in range(7)
]


class _Row:
    """Riga `BusinessHour` finta."""

    def __init__(
        self,
        dow: int,
        *,
        is_open: bool = True,
        open_h: str | None = "09:00",
        close_h: str | None = "18:00",
        break_start: str | None = None,
        break_end: str | None = None,
    ) -> None:
        def t(v: str | None) -> dt.time | None:
            return None if v is None else dt.time.fromisoformat(v)

        self.day_of_week = dow
        self.is_open = is_open
        self.open_time = t(open_h)
        self.close_time = t(close_h)
        self.break_start = t(break_start)
        self.break_end = t(break_end)


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    values: dict[ConfigKey, Any],
    rows: list[Any] | None = None,
    closures: list[dt.date] | None = None,
    resolver_raises: bool = False,
) -> None:
    class FakeResolver:
        def __init__(self, session: Any) -> None: ...

        async def resolve(self, key: Any, *, merchant_id: Any) -> Any:
            if resolver_raises:
                raise RuntimeError("cascata giù")
            return values.get(key)

    monkeypatch.setattr(rh, "ConfigResolver", FakeResolver)

    import db.repositories.services as svc

    class FakeHours:
        def __init__(self, session: Any) -> None: ...

        async def list(self, merchant_id: Any) -> list[Any]:
            return rows or []

    class FakeClosure:
        def __init__(self, session: Any) -> None: ...

        async def list(self, merchant_id: Any, *, from_date: Any = None) -> list[Any]:
            return [type("C", (), {"closed_on": d})() for d in (closures or [])]

    monkeypatch.setattr(svc, "BusinessHourRepository", FakeHours)
    monkeypatch.setattr(svc, "BusinessClosureRepository", FakeClosure)


def _base(mode: str) -> dict[ConfigKey, Any]:
    return {
        ConfigKey.SCHEDULE_MODE: mode,
        ConfigKey.SCHEDULE_TIMEZONE: "Europe/Rome",
        ConfigKey.SCHEDULE_OFF_HOURS_MESSAGE: "Siamo chiusi",
        ConfigKey.SCHEDULE_OFF_HOURS_MESSAGE_ONCE: True,
        ConfigKey.SCHEDULE_RESUME_ON_OPEN: True,
        ConfigKey.SCHEDULE_APPLY_TO_AUTOMATIONS: False,
        ConfigKey.SCHEDULE_WEEKLY: _WEEKLY_9_18,
    }


# --- modalità "sempre attivo" ----------------------------------------------


@pytest.mark.asyncio
async def test_always_mode_is_unrestricted(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, values=_base("always"))
    hours = await rh.resolve_response_hours(object(), MERCHANT)
    assert hours.unrestricted is True
    assert hours.is_open(NIGHT) is True
    assert hours.next_opening(NIGHT) == NIGHT


@pytest.mark.asyncio
async def test_unknown_mode_degrades_to_always(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, values=_base("qualcosa_di_ignoto"))
    assert (await rh.resolve_response_hours(object(), MERCHANT)).is_open(NIGHT) is True


# --- modalità "custom" ------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_mode_applies_the_weekly_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, values=_base("custom"))
    hours = await rh.resolve_response_hours(object(), MERCHANT)
    assert hours.unrestricted is False
    assert hours.is_open(NOON) is True
    assert hours.is_open(NIGHT) is False


@pytest.mark.asyncio
async def test_custom_mode_ignores_windows_of_disabled_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un giorno spento conserva i suoi orari ma non apre.

    Serve perché riattivare il giovedì non costringa a riscrivere gli orari
    che c'erano prima.
    """
    values = _base("custom")
    values[ConfigKey.SCHEDULE_WEEKLY] = [
        {"day": d, "enabled": False, "windows": [{"start": "09:00", "end": "18:00"}]}
        for d in range(7)
    ]
    _patch(monkeypatch, values=values)
    # Nessun giorno aperto → configurazione inservibile → fail-open.
    assert (await rh.resolve_response_hours(object(), MERCHANT)).is_open(NOON) is True


@pytest.mark.asyncio
async def test_custom_mode_with_empty_weekly_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _base("custom")
    values[ConfigKey.SCHEDULE_WEEKLY] = []
    _patch(monkeypatch, values=values)
    assert (await rh.resolve_response_hours(object(), MERCHANT)).is_open(NIGHT) is True


@pytest.mark.asyncio
async def test_custom_mode_survives_a_malformed_bag(monkeypatch: pytest.MonkeyPatch) -> None:
    values = _base("custom")
    values[ConfigKey.SCHEDULE_WEEKLY] = "non è una lista"
    _patch(monkeypatch, values=values)
    assert (await rh.resolve_response_hours(object(), MERCHANT)).is_open(NIGHT) is True


# --- modalità "orari di apertura" ------------------------------------------


@pytest.mark.asyncio
async def test_business_hours_mode_reads_the_opening_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, values=_base("business_hours"), rows=[_Row(d) for d in range(5)])
    hours = await rh.resolve_response_hours(object(), MERCHANT)
    assert hours.is_open(NOON) is True
    assert hours.is_open(NIGHT) is False


@pytest.mark.asyncio
async def test_business_hours_mode_splits_the_lunch_break(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La pausa del negozio è anche pausa del bot: due finestre, non una."""
    _patch(
        monkeypatch,
        values=_base("business_hours"),
        rows=[_Row(3, open_h="09:00", close_h="19:00", break_start="13:00", break_end="15:00")],
    )
    hours = await rh.resolve_response_hours(object(), MERCHANT)
    assert hours.is_open(dt.datetime(2026, 1, 15, 10, 0, tzinfo=dt.UTC)) is True  # 11:00
    assert hours.is_open(dt.datetime(2026, 1, 15, 13, 0, tzinfo=dt.UTC)) is False  # 14:00
    assert hours.is_open(dt.datetime(2026, 1, 15, 15, 0, tzinfo=dt.UTC)) is True  # 16:00


@pytest.mark.asyncio
async def test_business_hours_mode_ignores_an_incoherent_break(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pausa a cavallo o invertita: meglio una finestra intera che un buco."""
    _patch(
        monkeypatch,
        values=_base("business_hours"),
        rows=[_Row(3, open_h="09:00", close_h="19:00", break_start="15:00", break_end="13:00")],
    )
    hours = await rh.resolve_response_hours(object(), MERCHANT)
    assert hours.is_open(dt.datetime(2026, 1, 15, 13, 0, tzinfo=dt.UTC)) is True


@pytest.mark.asyncio
async def test_business_hours_mode_honours_closures(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        values=_base("business_hours"),
        rows=[_Row(d) for d in range(7)],
        closures=[dt.date(2026, 1, 15)],
    )
    assert (await rh.resolve_response_hours(object(), MERCHANT)).is_open(NOON) is False


@pytest.mark.asyncio
async def test_business_hours_mode_without_rows_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Segui gli orari di apertura" senza orari inseriti è una config
    incompleta, non la volontà di stare zitti."""
    _patch(monkeypatch, values=_base("business_hours"), rows=[])
    hours = await rh.resolve_response_hours(object(), MERCHANT)
    assert hours.unrestricted is True
    assert hours.is_open(NIGHT) is True


@pytest.mark.asyncio
async def test_business_hours_mode_skips_closed_days(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        values=_base("business_hours"),
        rows=[_Row(3, is_open=False), _Row(4)],
    )
    assert (await rh.resolve_response_hours(object(), MERCHANT)).is_open(NOON) is False


# --- difese -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_failure_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, values={}, resolver_raises=True)
    hours = await rh.resolve_response_hours(object(), MERCHANT)
    assert hours.unrestricted is True
    assert hours.is_open(NIGHT) is True


@pytest.mark.asyncio
async def test_flags_are_carried_through(monkeypatch: pytest.MonkeyPatch) -> None:
    values = _base("custom")
    values[ConfigKey.SCHEDULE_RESUME_ON_OPEN] = False
    values[ConfigKey.SCHEDULE_OFF_HOURS_MESSAGE_ONCE] = False
    values[ConfigKey.SCHEDULE_APPLY_TO_AUTOMATIONS] = True
    _patch(monkeypatch, values=values)
    hours = await rh.resolve_response_hours(object(), MERCHANT)
    assert hours.resume_on_open is False
    assert hours.off_hours_message_once is False
    assert hours.apply_to_automations is True
    assert hours.off_hours_message == "Siamo chiusi"


@pytest.mark.asyncio
async def test_blank_off_hours_message_becomes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Messaggio vuoto = non mandare nulla, non mandare una bolla bianca."""
    values = _base("custom")
    values[ConfigKey.SCHEDULE_OFF_HOURS_MESSAGE] = "   "
    _patch(monkeypatch, values=values)
    assert (await rh.resolve_response_hours(object(), MERCHANT)).off_hours_message is None


@pytest.mark.asyncio
async def test_next_opening_is_the_following_morning(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, values=_base("custom"))
    hours = await rh.resolve_response_hours(object(), MERCHANT)
    nxt = hours.next_opening(NIGHT)
    assert nxt is not None
    local = nxt.astimezone(hours.tz)
    assert (local.date(), local.hour) == (dt.date(2026, 1, 16), 9)
