"""Conversione bidirezionale tra i nostri modelli orari e il formato GHL openHours.

GHL usa la convenzione JS per i giorni della settimana (0=Domenica, 1=Lunedì,
…, 6=Sabato). Noi usiamo la convenzione ISO-1 (0=Lunedì, …, 6=Domenica).

Conversione:
  nostro → GHL:  ghl_day = (our_day + 1) % 7
  GHL → nostro:  our_day = (ghl_day + 6) % 7   (equivalente a (ghl_day - 1) % 7)

Le pause pranzo sono modellate come due slot `hours` separati nello stesso
giorno GHL — GHL li supporta nativamente (array `hours` per entry).
Le chiusure eccezionali corrispondono a `dateOverrides` con `hours: []`.
"""

from __future__ import annotations

import datetime
from typing import Any


# ── our → GHL ────────────────────────────────────────────────────────────────


def to_ghl_open_hours(hours: list[Any]) -> list[dict[str, Any]]:
    """Converti le righe `business_hours` nel formato `openHours` di GHL.

    I giorni con lo stesso orario (open_time, close_time, break_start,
    break_end) vengono raggruppati in un unico entry per ridurre il payload.
    I giorni chiusi non compaiono in `openHours` (GHL li considera chiusi
    implicitamente).
    """
    groups: dict[tuple[Any, ...], list[int]] = {}
    for h in hours:
        if not h.is_open or h.open_time is None or h.close_time is None:
            continue
        key = (h.open_time, h.close_time, h.break_start, h.break_end)
        ghl_day = (h.day_of_week + 1) % 7
        groups.setdefault(key, []).append(ghl_day)

    result: list[dict[str, Any]] = []
    for (open_t, close_t, break_s, break_e), days in groups.items():
        if break_s and break_e:
            slots = [
                {
                    "openHour": open_t.hour,
                    "openMinute": open_t.minute,
                    "closeHour": break_s.hour,
                    "closeMinute": break_s.minute,
                },
                {
                    "openHour": break_e.hour,
                    "openMinute": break_e.minute,
                    "closeHour": close_t.hour,
                    "closeMinute": close_t.minute,
                },
            ]
        else:
            slots = [
                {
                    "openHour": open_t.hour,
                    "openMinute": open_t.minute,
                    "closeHour": close_t.hour,
                    "closeMinute": close_t.minute,
                }
            ]
        result.append({"daysOfTheWeek": sorted(days), "hours": slots})
    return result


def to_ghl_date_overrides(closures: list[Any]) -> list[dict[str, Any]]:
    """Converti le righe `business_closures` nel formato `dateOverrides` di GHL.

    Solo le chiusure future vengono inviate — le passate non servono a GHL.
    """
    today = datetime.date.today()
    return [
        {"date": c.closed_on.isoformat(), "hours": []}
        for c in closures
        if c.closed_on >= today
    ]


# ── GHL → our ────────────────────────────────────────────────────────────────


def from_ghl_open_hours(ghl_hours: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converti `openHours` GHL in una lista di dict pronti per BusinessHour.

    Restituisce sempre 7 righe (una per giorno, 0=Lun…6=Dom). I giorni non
    menzionati in `openHours` vengono marcati `is_open=False`.
    """
    day_map: dict[int, dict[str, Any]] = {
        i: {
            "day_of_week": i,
            "is_open": False,
            "open_time": None,
            "close_time": None,
            "break_start": None,
            "break_end": None,
        }
        for i in range(7)
    }

    for entry in ghl_hours:
        ghl_days: list[int] = entry.get("daysOfTheWeek") or []
        slots: list[dict[str, Any]] = entry.get("hours") or []

        for ghl_day in ghl_days:
            our_day = (ghl_day + 6) % 7  # GHL 0=Sun → our 6; GHL 1=Mon → our 0
            row = day_map[our_day]

            if not slots:
                row["is_open"] = False
                continue

            row["is_open"] = True
            if len(slots) == 1:
                s = slots[0]
                row["open_time"] = datetime.time(s["openHour"], s["openMinute"])
                row["close_time"] = datetime.time(s["closeHour"], s["closeMinute"])
            else:
                # ≥2 slot → primo apre, ultimo chiude, break in mezzo
                s0, s1 = slots[0], slots[-1]
                row["open_time"] = datetime.time(s0["openHour"], s0["openMinute"])
                row["close_time"] = datetime.time(s1["closeHour"], s1["closeMinute"])
                # Il break è tra la chiusura del primo slot e l'apertura dell'ultimo.
                sm = slots[len(slots) // 2 - 1]  # slot immediatamente prima della pausa
                sn = slots[len(slots) // 2]       # slot immediatamente dopo la pausa
                row["break_start"] = datetime.time(sm["closeHour"], sm["closeMinute"])
                row["break_end"] = datetime.time(sn["openHour"], sn["openMinute"])

    return list(day_map.values())


def from_ghl_date_overrides(ghl_overrides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converti `dateOverrides` GHL in una lista di dict per BusinessClosure.

    Importa solo le chiusure full-day (hours=[]) future o presenti.
    Le date con orari speciali (hours non vuoto) vengono ignorate — le
    gestiamo solo come orari di apertura settimanali, non come eccezioni.
    """
    today = datetime.date.today()
    result: list[dict[str, Any]] = []
    for o in ghl_overrides:
        raw = o.get("date") or ""
        slots = o.get("hours") or []
        if slots:  # orario speciale, non chiusura — skip
            continue
        date_str = raw[:10]
        try:
            closed_on = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        if closed_on < today:
            continue
        result.append({"closed_on": closed_on, "label": None})
    return result
