"""UC-03 — no-answer follow-up scheduler unit tests.

Stubs the DB + WhatsApp send. Checks that:
  - A candidate idle past first_reminder_min gets a reminder #1.
  - A candidate whose last_reminder was too recent does NOT re-trigger.
  - A candidate at max_followups is skipped.
  - Redis dedup prevents a double send when invoked back-to-back.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest
from workers.scheduler import no_answer

from db import ReminderCandidate, ResolvedWhatsAppIntegration


@dataclass
class FakeRedis:
    _store: dict = field(default_factory=dict)

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True


@pytest.fixture
def integration() -> ResolvedWhatsAppIntegration:
    return ResolvedWhatsAppIntegration(
        merchant_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        phone_number_id="PNID-1",
        access_token="tok",
        meta={},
    )


@pytest.fixture
def candidate(integration: ResolvedWhatsAppIntegration) -> ReminderCandidate:
    return ReminderCandidate(
        conversation_id=uuid.uuid4(),
        merchant_id=integration.merchant_id,
        tenant_id=integration.tenant_id,
        wa_phone_number_id=integration.phone_number_id,
        wa_contact_phone="39333000000",
        last_message_at=datetime.now(tz=UTC) - timedelta(hours=3),
        reminders_sent=0,
        last_reminder_at=None,
    )


def _patch_session(
    monkeypatch,
    *,
    integration: ResolvedWhatsAppIntegration | None,
    config_overrides: dict | None = None,
    record_reminder_calls: list | None = None,
    analytics_events: list | None = None,
):
    config_values = {
        "no_answer.first_reminder_min": 120,
        "no_answer.second_reminder_min": 1440,
        "no_answer.max_followups": 2,
    }
    if config_overrides:
        config_values.update(config_overrides)

    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield object()

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    class FakeConfigResolver:
        def __init__(self, session): ...
        async def resolve(self, key, *, merchant_id):
            return config_values.get(getattr(key, "value", str(key)))

    class FakeIntegrationRepo:
        def __init__(self, session, *, kek_base64): ...
        async def resolve_whatsapp(self, phone_number_id):
            return integration

    class FakeConvRepo:
        def __init__(self, session): ...
        async def list_reminder_candidates(self, *, max_followups, min_idle_minutes=30):
            return []

        async def record_reminder_sent(self, conversation_id):
            if record_reminder_calls is not None:
                record_reminder_calls.append(conversation_id)

    class FakeAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kw):
            if analytics_events is not None:
                analytics_events.append(kw)

    class FakeWAClient:
        def __init__(self, *, access_token, phone_number_id): ...
        async def send_text(self, *, to_phone, text):
            return {"messages": [{"id": "wamid.ok"}]}

        async def close(self): ...

    monkeypatch.setattr(no_answer, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(no_answer, "session_scope", fake_session_scope)
    monkeypatch.setattr(no_answer, "ConfigResolver", FakeConfigResolver)
    monkeypatch.setattr(no_answer, "IntegrationRepository", FakeIntegrationRepo)
    monkeypatch.setattr(no_answer, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(no_answer, "AnalyticsRepository", FakeAnalyticsRepo)
    monkeypatch.setattr(no_answer, "WhatsAppClient", FakeWAClient)


# ---- tests ---------------------------------------------------------------


async def test_sends_first_reminder_when_idle_past_threshold(
    monkeypatch: pytest.MonkeyPatch, candidate, integration
) -> None:
    record_calls: list = []
    events: list = []
    _patch_session(
        monkeypatch,
        integration=integration,
        record_reminder_calls=record_calls,
        analytics_events=events,
    )

    did_send = await no_answer._maybe_send_reminder(candidate, redis=FakeRedis(), kek="unused")

    assert did_send is True
    assert record_calls == [candidate.conversation_id]
    assert events and events[0]["event_type"] == "reminder.sent"
    assert events[0]["properties"]["attempt"] == 1


async def test_skips_when_last_reminder_too_recent(
    monkeypatch: pytest.MonkeyPatch, candidate, integration
) -> None:
    fresh_candidate = replace(
        candidate,
        reminders_sent=1,
        last_reminder_at=datetime.now(tz=UTC),
    )
    record_calls: list = []
    _patch_session(monkeypatch, integration=integration, record_reminder_calls=record_calls)

    did_send = await no_answer._maybe_send_reminder(
        fresh_candidate, redis=FakeRedis(), kek="unused"
    )
    assert did_send is False
    assert record_calls == []


async def test_skips_when_at_max_followups(
    monkeypatch: pytest.MonkeyPatch, candidate, integration
) -> None:
    capped = replace(candidate, reminders_sent=2)
    record_calls: list = []
    _patch_session(monkeypatch, integration=integration, record_reminder_calls=record_calls)

    assert await no_answer._maybe_send_reminder(capped, redis=FakeRedis(), kek="unused") is False
    assert record_calls == []


async def test_redis_dedup_prevents_double_send(
    monkeypatch: pytest.MonkeyPatch, candidate, integration
) -> None:
    record_calls: list = []
    _patch_session(monkeypatch, integration=integration, record_reminder_calls=record_calls)
    redis = FakeRedis()

    first = await no_answer._maybe_send_reminder(candidate, redis=redis, kek="unused")
    second = await no_answer._maybe_send_reminder(candidate, redis=redis, kek="unused")

    assert first is True
    assert second is False
    assert len(record_calls) == 1
