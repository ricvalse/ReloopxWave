"""Orari di risposta — evaluator della settimana-tipo (logica pura).

Copre i casi che il vecchio formato a fascia unica non sapeva nemmeno
esprimere: giorni diversi fra loro, pausa pranzo, chiusure straordinarie,
scavalco della mezzanotte e cambio dell'ora legale.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from ai_core.scheduling import (
    TimeWindow,
    WeeklySchedule,
    always_open,
    is_open_at,
    is_within_active_hours,
    next_opening_after,
    resolve_timezone,
    schedule_from_windows,
)

ROME = resolve_timezone("Europe/Rome")

# Gennaio → Europe/Rome è UTC+1: questi istanti UTC corrispondono all'ora
# locale 12:00, 20:00 e 23:00.
NOON = datetime(2026, 1, 15, 11, 0, tzinfo=UTC)  # giovedì
EVENING = datetime(2026, 1, 15, 19, 0, tzinfo=UTC)
LATE = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)


def _weekdays_9_18() -> WeeklySchedule:
    return schedule_from_windows({d: [("09:00", "18:00")] for d in range(5)})


# --- apertura semplice ------------------------------------------------------


def test_always_open_is_open_at_any_hour() -> None:
    sched = always_open()
    for moment in (NOON, EVENING, LATE):
        assert is_open_at(sched, ROME, moment) is True


def test_weekday_window_open_at_noon_closed_in_the_evening() -> None:
    sched = _weekdays_9_18()
    assert is_open_at(sched, ROME, NOON) is True
    assert is_open_at(sched, ROME, EVENING) is False


def test_closed_day_is_closed_all_day() -> None:
    # Domenica 18 gennaio 2026, mezzogiorno locale.
    sunday_noon = datetime(2026, 1, 18, 11, 0, tzinfo=UTC)
    assert is_open_at(_weekdays_9_18(), ROME, sunday_noon) is False


def test_days_can_differ_from_each_other() -> None:
    """Il sabato chiude prima: è ciò che la fascia unica non sapeva dire."""
    sched = schedule_from_windows(
        {**{d: [("09:00", "19:00")] for d in range(5)}, 5: [("09:00", "13:00")]}
    )
    saturday_1130 = datetime(2026, 1, 17, 10, 30, tzinfo=UTC)
    saturday_1500 = datetime(2026, 1, 17, 14, 0, tzinfo=UTC)
    friday_1500 = datetime(2026, 1, 16, 14, 0, tzinfo=UTC)
    assert is_open_at(sched, ROME, saturday_1130) is True
    assert is_open_at(sched, ROME, saturday_1500) is False
    assert is_open_at(sched, ROME, friday_1500) is True


# --- pausa pranzo -----------------------------------------------------------


def test_lunch_break_closes_the_middle_of_the_day() -> None:
    sched = schedule_from_windows({3: [("09:00", "13:00"), ("15:00", "19:00")]})
    morning = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)  # 11:00 locali
    lunch = datetime(2026, 1, 15, 13, 0, tzinfo=UTC)  # 14:00 locali
    afternoon = datetime(2026, 1, 15, 15, 0, tzinfo=UTC)  # 16:00 locali
    assert is_open_at(sched, ROME, morning) is True
    assert is_open_at(sched, ROME, lunch) is False
    assert is_open_at(sched, ROME, afternoon) is True


def test_next_opening_from_inside_the_lunch_break_is_the_afternoon() -> None:
    sched = schedule_from_windows({3: [("09:00", "13:00"), ("15:00", "19:00")]})
    lunch = datetime(2026, 1, 15, 13, 0, tzinfo=UTC)
    nxt = next_opening_after(sched, ROME, lunch)
    assert nxt is not None
    assert nxt.astimezone(ROME).hour == 15


# --- scavalco della mezzanotte ---------------------------------------------


def test_overnight_window_is_open_after_midnight() -> None:
    """22:00-06:00 deve coprire le 2 di notte, che appartengono al giorno dopo."""
    sched = schedule_from_windows({d: [("22:00", "06:00")] for d in range(7)})
    at_2300 = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)  # 23:00 locali, giovedì
    at_0200 = datetime(2026, 1, 16, 1, 0, tzinfo=UTC)  # 02:00 locali, venerdì
    at_1200 = datetime(2026, 1, 16, 11, 0, tzinfo=UTC)  # 12:00 locali
    assert is_open_at(sched, ROME, at_2300) is True
    assert is_open_at(sched, ROME, at_0200) is True
    assert is_open_at(sched, ROME, at_1200) is False


def test_zero_length_window_covers_the_full_day() -> None:
    sched = WeeklySchedule(days=dict.fromkeys(range(7), (TimeWindow(time(0, 0), time(0, 0)),)))
    assert is_open_at(sched, ROME, LATE) is True


# --- chiusure straordinarie -------------------------------------------------


def test_closure_date_cancels_that_day() -> None:
    sched = schedule_from_windows(
        {d: [("09:00", "18:00")] for d in range(7)},
        closures=frozenset({date(2026, 1, 15)}),
    )
    assert is_open_at(sched, ROME, NOON) is False
    # Il giorno dopo è di nuovo aperto.
    assert is_open_at(sched, ROME, datetime(2026, 1, 16, 11, 0, tzinfo=UTC)) is True


def test_next_opening_skips_the_closure() -> None:
    sched = schedule_from_windows(
        {d: [("09:00", "18:00")] for d in range(7)},
        closures=frozenset({date(2026, 1, 16)}),
    )
    friday_eve = datetime(2026, 1, 15, 20, 0, tzinfo=UTC)  # giovedì 21:00 locali
    nxt = next_opening_after(sched, ROME, friday_eve)
    assert nxt is not None
    local = nxt.astimezone(ROME)
    assert (local.date(), local.hour) == (date(2026, 1, 17), 9)  # salta il 16


# --- prossima apertura ------------------------------------------------------


def test_next_opening_is_now_when_already_open() -> None:
    assert next_opening_after(_weekdays_9_18(), ROME, NOON) == NOON


def test_next_opening_is_tomorrow_morning_after_closing_time() -> None:
    nxt = next_opening_after(_weekdays_9_18(), ROME, EVENING)
    assert nxt is not None
    local = nxt.astimezone(ROME)
    assert (local.date(), local.hour, local.minute) == (date(2026, 1, 16), 9, 0)


def test_next_opening_jumps_the_weekend_to_monday() -> None:
    friday_evening = datetime(2026, 1, 16, 19, 0, tzinfo=UTC)  # venerdì 20:00 locali
    nxt = next_opening_after(_weekdays_9_18(), ROME, friday_evening)
    assert nxt is not None
    local = nxt.astimezone(ROME)
    assert local.weekday() == 0  # lunedì
    assert (local.date(), local.hour) == (date(2026, 1, 19), 9)


def test_next_opening_is_none_when_the_week_is_all_closed() -> None:
    """Distinguere «riapre domani» da «non riapre mai» è ciò che impedisce
    allo sweep di trattenere una conversazione all'infinito."""
    assert next_opening_after(WeeklySchedule(days={}), ROME, NOON) is None


# --- fuso orario e ora legale ----------------------------------------------


def test_invalid_timezone_falls_back_to_rome() -> None:
    assert resolve_timezone("Not/AZone").key == "Europe/Rome"
    assert resolve_timezone(None).key == "Europe/Rome"


def test_opening_follows_the_wall_clock_across_dst() -> None:
    """Le 09:00 restano le 09:00 locali anche dopo il cambio dell'ora.

    In Italia l'ora legale 2026 scatta il 29 marzo: prima Roma è UTC+1, dopo
    UTC+2. Un evaluator che ragionasse su offset fissi sposterebbe l'apertura
    di un'ora, e il bot resterebbe muto per la prima ora di ogni lunedì
    successivo al cambio.
    """
    sched = schedule_from_windows({d: [("09:00", "18:00")] for d in range(7)})
    # 28 marzo (ora solare): 08:00Z = 09:00 locali → aperto.
    assert is_open_at(sched, ROME, datetime(2026, 3, 28, 8, 0, tzinfo=UTC)) is True
    assert is_open_at(sched, ROME, datetime(2026, 3, 28, 7, 30, tzinfo=UTC)) is False
    # 30 marzo (ora legale): 07:00Z = 09:00 locali → aperto.
    assert is_open_at(sched, ROME, datetime(2026, 3, 30, 7, 0, tzinfo=UTC)) is True
    assert is_open_at(sched, ROME, datetime(2026, 3, 30, 6, 30, tzinfo=UTC)) is False


# --- parsing ----------------------------------------------------------------


def test_unparseable_windows_are_dropped_not_fatal() -> None:
    sched = schedule_from_windows({0: [("09:00", "18:00"), ("boom", "18:00")]})
    assert len(sched.days[0]) == 1


def test_out_of_range_weekday_is_ignored() -> None:
    assert schedule_from_windows({9: [("09:00", "18:00")]}).days == {}


# --- formato storico (usato solo dalla migrazione 0049) ---------------------


def test_legacy_always_on_variants() -> None:
    for spec in ("24/7", "", "always", None):
        assert is_within_active_hours(spec, "Europe/Rome", EVENING) is True


def test_legacy_matches_the_migrated_weekly_schedule() -> None:
    """La 0049 non deve cambiare il comportamento di chi aveva già una fascia.

    Golden test della conversione: per ogni ora del giorno il vecchio parser e
    la settimana-tipo migrata (stessa fascia su tutti e sette i giorni) devono
    dare la stessa risposta.
    """
    for spec, windows in (("09:00-18:00", ("09:00", "18:00")), ("22:00-06:00", ("22:00", "06:00"))):
        migrated = schedule_from_windows({d: [windows] for d in range(7)})
        for hour in range(24):
            moment = datetime(2026, 1, 15, hour, 0, tzinfo=UTC)
            assert is_within_active_hours(spec, "Europe/Rome", moment) == is_open_at(
                migrated, ROME, moment
            ), f"{spec} diverge alle {hour}:00Z"


def test_legacy_unparseable_fails_open() -> None:
    assert is_within_active_hours("nonsense", "Europe/Rome", EVENING) is True
    assert is_within_active_hours("9-18", "Europe/Rome", EVENING) is True
