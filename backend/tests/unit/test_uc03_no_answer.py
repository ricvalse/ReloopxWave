"""UC-03 — no-answer trigger emitter unit tests (ADR 0015).

The scheduler no longer sends: it emits a `lead.no_answer` analytics event once
per silence episode (edge-triggered on `last_inbound_at`), which the automation
engine dispatches to the merchant's flow. These tests stub the repos and check:
  - idle past the trigger's `delay_minutes` + an enabled automation → emits + anchors;
  - no enabled `no_answer` automation → nothing emitted (and no anchor burned);
  - already fired for this silence episode → suppressed;
  - a fresh inbound (last_inbound_at past the old anchor) re-arms the trigger;
  - still within the configured delay → not yet emitted.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from workers.scheduler import no_answer

from db import ReminderCandidate


def _fake_flow(delay_minutes: int = 120) -> Any:
    trigger = SimpleNamespace(
        kind="trigger", type="no_answer", config={"delay_minutes": delay_minutes}
    )
    return SimpleNamespace(nodes=[trigger], edges=[])


def _candidate(**over: Any) -> ReminderCandidate:
    now = datetime.now(tz=UTC)
    base: dict[str, Any] = dict(
        conversation_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        wa_phone_number_id="PNID-1",
        wa_contact_phone="39333000000",
        last_message_at=now - timedelta(hours=3),
        reminders_sent=0,
        last_reminder_at=None,
        last_inbound_at=now - timedelta(hours=3),
        no_answer_fired_for=None,
    )
    base.update(over)
    return ReminderCandidate(**base)


def _patch(monkeypatch: pytest.MonkeyPatch, *, flows: list, marks: list, events: list) -> None:
    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield object()

    class FakeAutoRepo:
        def __init__(self, session): ...
        async def list_enabled_by_trigger(self, *, merchant_id, trigger_type):
            assert trigger_type == "no_answer"
            return flows

    class FakeConvRepo:
        def __init__(self, session): ...
        async def mark_no_answer_fired(self, conversation_id, anchor):
            marks.append((conversation_id, anchor))

    class FakeAnalytics:
        def __init__(self, session): ...
        async def emit(self, **kw):
            events.append(kw)

    monkeypatch.setattr(no_answer, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(no_answer, "AutomationRepository", FakeAutoRepo)
    monkeypatch.setattr(no_answer, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(no_answer, "AnalyticsRepository", FakeAnalytics)


async def test_emits_when_idle_past_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow(120)], marks=marks, events=events)
    cand = _candidate()  # idle 3h > 120 min

    ok = await no_answer._maybe_emit(cand, now=datetime.now(tz=UTC))

    assert ok is True
    assert marks == [(cand.conversation_id, cand.last_inbound_at)]
    assert events and events[0]["event_type"] == "lead.no_answer"
    assert events[0]["subject_type"] == "conversation"
    assert events[0]["subject_id"] == cand.conversation_id
    assert events[0]["properties"]["episode_anchor"] == cand.last_inbound_at.isoformat()


async def test_skips_when_no_enabled_automation(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[], marks=marks, events=events)

    ok = await no_answer._maybe_emit(_candidate(), now=datetime.now(tz=UTC))

    assert ok is False
    assert marks == [] and events == []


async def test_skips_when_already_fired_for_episode(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow()], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    anchor = now - timedelta(hours=3)
    cand = _candidate(last_inbound_at=anchor, no_answer_fired_for=anchor)

    ok = await no_answer._maybe_emit(cand, now=now)

    assert ok is False
    assert events == []


async def test_re_arms_after_new_inbound(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow()], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    # A new inbound arrived AFTER the previous fired anchor → the trigger re-arms.
    cand = _candidate(
        last_message_at=now - timedelta(hours=3),
        last_inbound_at=now - timedelta(hours=2),
        no_answer_fired_for=now - timedelta(hours=5),
    )

    ok = await no_answer._maybe_emit(cand, now=now)

    assert ok is True
    assert events and events[0]["event_type"] == "lead.no_answer"


async def test_skips_when_not_idle_past_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow(600)], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    # Idle 180 min < the trigger's 600-min delay → not yet due.
    cand = _candidate(
        last_message_at=now - timedelta(minutes=180),
        last_inbound_at=now - timedelta(minutes=180),
    )

    ok = await no_answer._maybe_emit(cand, now=now)

    assert ok is False
    assert events == []
