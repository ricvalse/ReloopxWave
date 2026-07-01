"""UC-06 — opt-out detection + dormant trigger emitter (ADR 0015).

Covers:
  * `_is_opt_out` — STOP/CANCELLA detection (exact, normalised) driving the
    opt-out intercept in `handle_inbound_persist`.
  * reactivation `_maybe_emit`: emits a `lead.dormant` event once per dormancy
    episode (edge-triggered on `last_interaction_at`) when an enabled `lead_dormant`
    automation exists and the lead has crossed its threshold; sends nothing itself.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from workers.scheduler import reactivation

from ai_core.conversation_service import _is_opt_out
from db import ReactivationCandidate


def test_is_opt_out_matches_exact_keywords() -> None:
    for msg in ["STOP", "stop", " Stop ", "CANCELLA", "annulla", "unsubscribe", "Stop."]:
        assert _is_opt_out(msg) is True


def test_is_opt_out_ignores_sentences() -> None:
    for msg in ["stop un attimo", "non cancellare", "vorrei fermare l'ordine", "ok grazie"]:
        assert _is_opt_out(msg) is False


# ---- reactivation trigger emitter -----------------------------------------


def _fake_flow(days: int = 90) -> Any:
    trigger = SimpleNamespace(kind="trigger", type="lead_dormant", config={"days": days})
    return SimpleNamespace(nodes=[trigger], edges=[])


def _candidate(**over: Any) -> ReactivationCandidate:
    now = datetime.now(tz=UTC)
    base: dict[str, Any] = dict(
        lead_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        phone="39333000000",
        wa_phone_number_id="PNID-1",
        last_interaction_at=now - timedelta(days=120),
        attempts_sent=0,
        last_reactivation_at=None,
        name="Mario",
        last_inbound_at=now - timedelta(days=120),
        dormant_fired_for=None,
    )
    base.update(over)
    return ReactivationCandidate(**base)


def _patch(monkeypatch: pytest.MonkeyPatch, *, flows: list, marks: list, events: list) -> None:
    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield object()

    class FakeAutoRepo:
        def __init__(self, session): ...
        async def list_enabled_by_trigger(self, *, merchant_id, trigger_type):
            assert trigger_type == "lead_dormant"
            return flows

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def mark_dormant_fired(self, lead_id, anchor):
            marks.append((lead_id, anchor))

    class FakeAnalytics:
        def __init__(self, session): ...
        async def emit(self, **kw):
            events.append(kw)

    monkeypatch.setattr(reactivation, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(reactivation, "AutomationRepository", FakeAutoRepo)
    monkeypatch.setattr(reactivation, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(reactivation, "AnalyticsRepository", FakeAnalytics)


async def test_emits_when_dormant_past_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow(90)], marks=marks, events=events)
    cand = _candidate()  # dormant 120 days > 90

    ok = await reactivation._maybe_emit(cand, now=datetime.now(tz=UTC))

    assert ok is True
    assert marks == [(cand.lead_id, cand.last_interaction_at)]
    assert events and events[0]["event_type"] == "lead.dormant"
    assert events[0]["subject_type"] == "lead"
    assert events[0]["subject_id"] == cand.lead_id


async def test_skips_when_no_enabled_automation(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[], marks=marks, events=events)

    ok = await reactivation._maybe_emit(_candidate(), now=datetime.now(tz=UTC))

    assert ok is False
    assert marks == [] and events == []


async def test_skips_when_already_fired_for_episode(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow()], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    anchor = now - timedelta(days=120)
    cand = _candidate(last_interaction_at=anchor, dormant_fired_for=anchor)

    ok = await reactivation._maybe_emit(cand, now=now)

    assert ok is False
    assert events == []


async def test_skips_when_not_dormant_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow(90)], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    # Dormant only 40 days < the trigger's 90-day threshold.
    cand = _candidate(last_interaction_at=now - timedelta(days=40))

    ok = await reactivation._maybe_emit(cand, now=now)

    assert ok is False
    assert events == []
