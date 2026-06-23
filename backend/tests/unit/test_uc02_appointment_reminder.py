"""UC-02 — appointment reminder scheduler unit tests.

Stubs the DB + WhatsApp send. Checks that:
  - A booked appointment whose lead is inside the 24h window gets a free-text
    reminder, is marked reminded, and emits the analytics event.
  - Outside the window with no approved template → skipped, not marked (so it
    retries once a template lands).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from workers.scheduler import appointment_reminder as mod

from db import AppointmentReminderCandidate, ResolvedWhatsAppIntegration

NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


def _candidate(*, last_inbound_at: datetime | None) -> AppointmentReminderCandidate:
    return AppointmentReminderCandidate(
        appointment_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        phone="39333000000",
        wa_phone_number_id="PNID-1",
        last_inbound_at=last_inbound_at,
        start_at=NOW + timedelta(hours=12),
        tz_name="Europe/Rome",
    )


def _patch(monkeypatch, *, marked: list, events: list, integration_present: bool = True) -> None:
    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield object()

    class FakeApptRepo:
        def __init__(self, session): ...
        async def mark_reminded(self, appointment_id, *, at):
            marked.append(appointment_id)

    async def fake_resolve_lifecycle_step(
        session, *, merchant_id, system_key, attempt_index, context
    ):
        return None  # no system flow → built-in free-text fallback

    class FakeIntegrationRepo:
        def __init__(self, session, *, kek_base64): ...
        async def resolve_whatsapp(self, phone_number_id):
            if not integration_present:
                return None
            return ResolvedWhatsAppIntegration(
                merchant_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                phone_number_id=phone_number_id,
                api_key="k",
                waba_base_url=None,
                meta={},
            )

    class FakeAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kw):
            events.append(kw)

    class FakeWAClient:
        async def send_text(self, *, to_phone, text):
            return {"messages": [{"id": "wamid.ok"}]}

        async def send_template(self, *, to_phone, template_name, language, components):
            return {"messages": [{"id": "wamid.tmpl"}]}

        async def close(self): ...

    monkeypatch.setattr(mod, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(mod, "AppointmentRepository", FakeApptRepo)
    monkeypatch.setattr(mod, "resolve_lifecycle_step", fake_resolve_lifecycle_step)
    monkeypatch.setattr(mod, "IntegrationRepository", FakeIntegrationRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", FakeAnalyticsRepo)
    monkeypatch.setattr(mod, "build_whatsapp_sender", lambda **kw: FakeWAClient())


async def test_sends_reminder_inside_window(monkeypatch: pytest.MonkeyPatch) -> None:
    marked: list = []
    events: list = []
    _patch(monkeypatch, marked=marked, events=events)

    cand = _candidate(last_inbound_at=NOW - timedelta(hours=2))  # inside 24h
    sent = await mod._maybe_send(cand, now=NOW, kek="unused")

    assert sent is True
    assert marked == [cand.appointment_id]
    assert events and events[0]["event_type"] == "appointment_reminder.sent"


async def test_skips_outside_window_without_template(monkeypatch: pytest.MonkeyPatch) -> None:
    marked: list = []
    events: list = []
    _patch(monkeypatch, marked=marked, events=events)

    cand = _candidate(last_inbound_at=NOW - timedelta(hours=30))  # outside 24h, no template
    sent = await mod._maybe_send(cand, now=NOW, kek="unused")

    assert sent is False
    assert marked == []  # not marked → retried when a template is approved
