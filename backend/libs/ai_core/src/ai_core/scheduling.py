"""Active-hours window (CC-CONFIG / UC-01).

`schedule.active_hours` lets a merchant restrict when the bot replies. Outside
the window the conversation pipeline sends `schedule.off_hours_message` instead
of generating an LLM reply.

Format accepted for `active_hours`:
  * "24/7" (or "always") — the bot is always on (the default).
  * "HH:MM-HH:MM" — a single daily window in the merchant's local timezone.
    Overnight windows (start > end, e.g. "22:00-06:00") are supported.

Parsing is deliberately lenient: anything we can't parse fails OPEN (treated as
always-on) so a config typo can never silence a merchant's bot entirely.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_ALWAYS = {"", "24/7", "24x7", "24-7", "always", "sempre"}


def _parse_hhmm(token: str) -> tuple[int, int]:
    hh, mm = token.strip().split(":", 1)
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"out-of-range time {token!r}")
    return h, m


def is_within_active_hours(active_hours: str | None, tz_name: str | None, now: datetime) -> bool:
    """True if `now` falls inside the merchant's active-hours window."""
    spec = (active_hours or "").strip().lower()
    if spec in _ALWAYS:
        return True

    try:
        start_token, end_token = spec.split("-", 1)
        start = _parse_hhmm(start_token)
        end = _parse_hhmm(end_token)
    except (ValueError, AttributeError):
        return True  # unparseable → fail open

    try:
        tz = ZoneInfo(tz_name or "Europe/Rome")
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("Europe/Rome")

    local = now.astimezone(tz)
    current = (local.hour, local.minute)

    if start == end:
        return True  # zero/full-day window → treat as always-on
    if start < end:
        return start <= current < end
    # Overnight window, e.g. 22:00-06:00.
    return current >= start or current < end
