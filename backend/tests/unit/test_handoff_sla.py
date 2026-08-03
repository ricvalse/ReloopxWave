"""Handoff SLA sweep unit tests — edge-triggered emitter (mirrors UC-03).

Stubs the repos and checks:
  - open handoff past the SLA + an enabled overdue automation → emits + anchors;
  - no enabled `conversation_handoff_overdue` automation → nothing emitted, and
    the anchor isn't burned;
  - already fired for this handoff episode (sla_fired_for >= handoff_at) → suppressed;
  - a re-escalation (a new handoff_at past the old anchor) re-arms the trigger;
  - la soglia è quella del merchant (`escalation.sla_minutes`), non più un
    valore d'ambiente unico per tutta la piattaforma.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from workers.scheduler import handoff_sla

from config_resolver import ConfigKey
from db import HandoffOverdueCandidate


def _cand(**over: Any) -> HandoffOverdueCandidate:
    now = datetime.now(tz=UTC)
    base: dict[str, Any] = {
        "conversation_id": uuid.uuid4(),
        "merchant_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "lead_id": uuid.uuid4(),
        "handoff_at": now - timedelta(minutes=30),
        "sla_fired_for": None,
    }
    base.update(over)
    return HandoffOverdueCandidate(**base)


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    flows: list[Any],
    marks: list[Any],
    events: list[Any],
    sla_minutes: Any = 15,
) -> None:
    @asynccontextmanager
    async def fake_tenant_session(ctx: Any) -> Any:
        yield object()

    class FakeConfigResolver:
        def __init__(self, session: Any) -> None: ...

        async def resolve(self, key: Any, *, merchant_id: Any) -> Any:
            assert key is ConfigKey.ESCALATION_SLA_MINUTES
            return sla_minutes

    monkeypatch.setattr(handoff_sla, "ConfigResolver", FakeConfigResolver)

    class FakeAutoRepo:
        def __init__(self, session: Any) -> None: ...

        async def list_enabled_by_trigger(
            self, *, merchant_id: Any, trigger_type: str
        ) -> list[Any]:
            assert trigger_type == "conversation_handoff_overdue"
            return flows

    class FakeConvRepo:
        def __init__(self, session: Any) -> None: ...

        async def mark_handoff_sla_fired(self, conversation_id: Any, anchor: Any) -> None:
            marks.append((conversation_id, anchor))

    class FakeAnalytics:
        def __init__(self, session: Any) -> None: ...

        async def emit(self, **kw: Any) -> None:
            events.append(kw)

    monkeypatch.setattr(handoff_sla, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(handoff_sla, "AutomationRepository", FakeAutoRepo)
    monkeypatch.setattr(handoff_sla, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(handoff_sla, "AnalyticsRepository", FakeAnalytics)


async def test_emits_for_overdue_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list[Any] = []
    events: list[Any] = []
    _patch(monkeypatch, flows=[object()], marks=marks, events=events)
    cand = _cand()

    ok = await handoff_sla._maybe_emit(cand, now=datetime.now(tz=UTC))

    assert ok is True
    assert marks == [(cand.conversation_id, cand.handoff_at)]
    assert events and events[0]["event_type"] == "conversation.handoff_overdue"
    assert events[0]["subject_type"] == "conversation"
    assert events[0]["subject_id"] == cand.conversation_id
    assert events[0]["properties"]["overdue_minutes"] >= 30


async def test_skips_when_no_enabled_automation(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list[Any] = []
    events: list[Any] = []
    _patch(monkeypatch, flows=[], marks=marks, events=events)

    ok = await handoff_sla._maybe_emit(_cand(), now=datetime.now(tz=UTC))

    assert ok is False
    assert marks == [] and events == []


async def test_skips_when_already_fired_for_episode(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list[Any] = []
    events: list[Any] = []
    _patch(monkeypatch, flows=[object()], marks=marks, events=events)
    ho = datetime.now(tz=UTC) - timedelta(minutes=30)
    cand = _cand(handoff_at=ho, sla_fired_for=ho)

    ok = await handoff_sla._maybe_emit(cand, now=datetime.now(tz=UTC))

    assert ok is False
    assert events == []


async def test_re_arms_on_new_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list[Any] = []
    events: list[Any] = []
    _patch(monkeypatch, flows=[object()], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    # A new handoff_at AFTER the previous fired anchor → the trigger re-arms.
    cand = _cand(handoff_at=now - timedelta(minutes=30), sla_fired_for=now - timedelta(hours=5))

    ok = await handoff_sla._maybe_emit(cand, now=datetime.now(tz=UTC))

    assert ok is True
    assert events and events[0]["event_type"] == "conversation.handoff_overdue"


async def test_respects_the_merchants_own_sla(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un handoff aperto da 10 minuti non è in ritardo per un merchant che si è
    dato 30 minuti di presidio. Prima la soglia era un'unica variabile
    d'ambiente e questo merchant non aveva modo di esprimerla."""
    marks: list[Any] = []
    events: list[Any] = []
    _patch(monkeypatch, flows=[object()], marks=marks, events=events, sla_minutes=30)
    cand = _cand(handoff_at=datetime.now(tz=UTC) - timedelta(minutes=10))

    ok = await handoff_sla._maybe_emit(cand, now=datetime.now(tz=UTC))

    assert ok is False
    # L'ancora NON va bruciata: appena la soglia scatta davvero, deve emettere.
    assert marks == [] and events == []


async def test_short_sla_fires_earlier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lo stesso handoff, per un merchant che vuole essere avvisato dopo 5 minuti."""
    marks: list[Any] = []
    events: list[Any] = []
    _patch(monkeypatch, flows=[object()], marks=marks, events=events, sla_minutes=5)
    cand = _cand(handoff_at=datetime.now(tz=UTC) - timedelta(minutes=10))

    ok = await handoff_sla._maybe_emit(cand, now=datetime.now(tz=UTC))

    assert ok is True
    assert events and events[0]["event_type"] == "conversation.handoff_overdue"


async def test_unusable_sla_falls_back_instead_of_firing_on_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una soglia non positiva spingerebbe il cutoff nel futuro e farebbe
    risultare scaduto ogni handoff aperto: si ripiega sul default."""
    marks: list[Any] = []
    events: list[Any] = []
    _patch(monkeypatch, flows=[object()], marks=marks, events=events, sla_minutes=0)
    cand = _cand(handoff_at=datetime.now(tz=UTC) - timedelta(minutes=1))

    ok = await handoff_sla._maybe_emit(cand, now=datetime.now(tz=UTC))

    assert ok is False  # 1 minuto < il default di 15
    assert events == []
