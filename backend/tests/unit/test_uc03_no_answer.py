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
from typing import ClassVar

import pytest
from workers import outbound
from workers.scheduler import no_answer

from db import ReminderCandidate, ResolvedFlowStep, ResolvedWhatsAppIntegration


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
        api_key="test-channel-key",
        waba_base_url=None,
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
        # Inside the 24h window → free-text reminder is allowed.
        last_inbound_at=datetime.now(tz=UTC) - timedelta(hours=3),
    )


class FakeMessageRepo:
    """Captures proactive outbound Message persistence (#29)."""

    persisted: ClassVar[list] = []

    def __init__(self, session): ...

    async def persist_outbound_message(self, **kw):
        FakeMessageRepo.persisted.append(kw)
        return object()


def _patch_session(
    monkeypatch,
    *,
    integration: ResolvedWhatsAppIntegration | None,
    config_overrides: dict | None = None,
    record_reminder_calls: list | None = None,
    analytics_events: list | None = None,
):
    FakeMessageRepo.persisted = []
    monkeypatch.setattr(outbound, "MessageRepository", FakeMessageRepo)
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

    async def fake_resolve_lifecycle_step(
        session, *, merchant_id, system_key, attempt_index, context
    ):
        # Enabled no-answer flow whose send node carries the merchant's configured
        # copy from the lavagnetta (ADR 0014: content comes only from the canvas).
        return ResolvedFlowStep(
            flow_enabled=True,
            step_enabled=True,
            window_policy="auto",
            free_text="Ciao {name}, ci sei ancora?",
            variable_mapping={},
            template_name=None,
            template_language=None,
        )

    async def fake_resolve_lifecycle_plan(session, *, merchant_id, system_key, context):
        # No enabled system flow → scheduler sources timing from ConfigKeys.
        return None

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

    def fake_factory(*, phone_number_id, api_key=None, waba_base_url=None):
        return FakeWAClient(access_token=api_key or "fake", phone_number_id=phone_number_id)

    monkeypatch.setattr(no_answer, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(no_answer, "session_scope", fake_session_scope)
    monkeypatch.setattr(no_answer, "ConfigResolver", FakeConfigResolver)
    monkeypatch.setattr(no_answer, "IntegrationRepository", FakeIntegrationRepo)
    monkeypatch.setattr(no_answer, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(no_answer, "AnalyticsRepository", FakeAnalyticsRepo)
    monkeypatch.setattr(no_answer, "resolve_lifecycle_step", fake_resolve_lifecycle_step)
    monkeypatch.setattr(no_answer, "resolve_lifecycle_plan", fake_resolve_lifecycle_plan)
    monkeypatch.setattr(no_answer, "build_whatsapp_sender", fake_factory)


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
    # #29: the proactive send is persisted as an outbound Message so it shows in
    # the inbox and the delivery callback can attach via wa_message_id.
    assert len(FakeMessageRepo.persisted) == 1
    persisted = FakeMessageRepo.persisted[0]
    assert persisted["conversation_id"] == candidate.conversation_id
    assert persisted["merchant_id"] == candidate.merchant_id
    assert persisted["wa_message_id"] == "wamid.ok"
    assert persisted["status"] == "sent"


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


async def test_graph_drives_threshold_and_max(
    monkeypatch: pytest.MonkeyPatch, candidate, integration
) -> None:
    """ADR 0011: an enabled system flow sources cadence/count from the graph.

    A 1-send plan with a 600-min initial delay must (a) override the config
    first-reminder threshold (120) and (b) cap max_followups at 1 (< config's 2).
    """
    from ai_core.automations import PlannedSend, SendPlan

    record_calls: list = []
    _patch_session(monkeypatch, integration=integration, record_reminder_calls=record_calls)

    async def one_send_plan(session, *, merchant_id, system_key, context):
        return SendPlan(
            sends=[
                PlannedSend(attempt_index=0, delay_minutes=600, anchor_hours_before=None, config={})
            ]
        )

    monkeypatch.setattr(no_answer, "resolve_lifecycle_plan", one_send_plan)

    # Idle 3h (180 min) < 600-min graph threshold → not yet due.
    assert await no_answer._maybe_send_reminder(candidate, redis=FakeRedis(), kek="unused") is False
    assert record_calls == []

    # Idle 11h (660 min) > 600 → due (still inside the 24h window → free text).
    aged = replace(
        candidate,
        last_message_at=datetime.now(tz=UTC) - timedelta(minutes=660),
        last_inbound_at=datetime.now(tz=UTC) - timedelta(minutes=660),
    )
    assert await no_answer._maybe_send_reminder(aged, redis=FakeRedis(), kek="unused") is True

    # Graph has a single send → attempt #2 is capped out (config max of 2 would allow it).
    capped = replace(candidate, reminders_sent=1)
    assert await no_answer._maybe_send_reminder(capped, redis=FakeRedis(), kek="unused") is False


async def test_no_flow_skips_and_not_recorded(
    monkeypatch: pytest.MonkeyPatch, candidate, integration
) -> None:
    # ADR 0014: no enabled no-answer flow on the canvas → no reminder is sent and
    # nothing is recorded/persisted.
    record_calls: list = []
    _patch_session(monkeypatch, integration=integration, record_reminder_calls=record_calls)

    async def no_flow(session, *, merchant_id, system_key, attempt_index, context):
        return None

    monkeypatch.setattr(no_answer, "resolve_lifecycle_step", no_flow)

    did_send = await no_answer._maybe_send_reminder(candidate, redis=FakeRedis(), kek="unused")

    assert did_send is False
    assert record_calls == []
    assert FakeMessageRepo.persisted == []


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
