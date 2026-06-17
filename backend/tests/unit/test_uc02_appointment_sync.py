"""UC-02 reconcile poll — pure helpers (no DB / no GHL).

Covers the defensive normalization of GHL's `GET /calendars/events` response
(envelope shape + epoch-ms vs ISO times) and the appointmentStatus mapping, the
two places a GHL wire-format surprise would otherwise silently corrupt the
mirror.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from workers.scheduler.appointment_sync import _map_status, _parse_iso

from integrations.ghl.client import _normalize_events


def test_normalize_events_envelope_iso_times() -> None:
    resp = {
        "events": [
            {
                "id": "evt-1",
                "calendarId": "CAL-1",
                "contactId": "CT-1",
                "title": "Consulenza",
                "startTime": "2026-07-15T15:00:00+02:00",
                "endTime": "2026-07-15T15:30:00+02:00",
                "appointmentStatus": "confirmed",
            }
        ],
        "traceId": "abc",
    }
    out = _normalize_events(resp)
    assert len(out) == 1
    ev = out[0]
    assert ev["id"] == "evt-1"
    assert ev["calendar_id"] == "CAL-1"
    assert ev["contact_id"] == "CT-1"
    assert ev["title"] == "Consulenza"
    assert ev["start_iso"] == "2026-07-15T15:00:00+02:00"
    assert ev["end_iso"] == "2026-07-15T15:30:00+02:00"
    assert ev["status"] == "confirmed"


def test_normalize_events_bare_list_and_epoch_ms() -> None:
    # startTime as epoch-ms int, endTime as epoch-ms string (+30 min).
    resp = [
        {"id": "evt-2", "startTime": 1784293200000, "endTime": "1784295000000"},
    ]
    out = _normalize_events(resp)
    assert len(out) == 1
    assert out[0]["id"] == "evt-2"
    # Both epoch-ms forms convert to UTC ISO strings the mirror can parse, and
    # the +30 min gap is preserved.
    start = datetime.fromisoformat(out[0]["start_iso"])
    end = datetime.fromisoformat(out[0]["end_iso"])
    assert start == datetime.fromtimestamp(1784293200000 / 1000, tz=UTC)
    assert end - start == timedelta(minutes=30)


def test_normalize_events_skips_idless_and_garbage() -> None:
    assert _normalize_events({}) == []
    assert _normalize_events({"events": [{"startTime": "x"}, "nope", 5]}) == []
    assert _normalize_events("unexpected") == []


def test_map_status_collapses_booked_states() -> None:
    assert _map_status("confirmed") == "booked"
    assert _map_status("new") == "booked"
    assert _map_status(None) == "booked"
    assert _map_status("") == "booked"
    # Non-booked states pass through verbatim (lowercased) so the agenda sees them.
    assert _map_status("cancelled") == "cancelled"
    assert _map_status("NoShow") == "noshow"


def test_parse_iso_handles_offset_naive_and_garbage() -> None:
    assert _parse_iso("2026-07-15T15:00:00+02:00").utcoffset().total_seconds() == 7200
    # naive → assumed UTC
    assert _parse_iso("2026-07-15T15:00:00").utcoffset().total_seconds() == 0
    assert _parse_iso(None) is None
    assert _parse_iso("not-a-date") is None
