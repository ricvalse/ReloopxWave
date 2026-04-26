"""UC-05 — update_score handler unit tests."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest

from ai_core.actions.scoring import (
    UpdateScoreHandler,
    _classify,
    derive_signals_from_llm_payload,
)
from ai_core.conversation_service import TurnContext
from ai_core.orchestrator import OrchestratorAction


@pytest.fixture
def turn_ctx() -> TurnContext:
    return TurnContext(
        tenant_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        lead_phone="39333000000",
        phone_number_id="PNID-1",
    )


@dataclass
class FakeLead:
    id: uuid.UUID
    score: int = 0


def _patch(monkeypatch, lead: FakeLead | None, captured_events: list, captured_updates: list):
    from ai_core.actions import scoring as mod

    @asynccontextmanager
    async def fake_session(ctx):
        yield object()

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def get_by_phone(self, *, merchant_id, phone):
            return lead
        async def update_score(self, lead_id, *, score, reasons):
            captured_updates.append({"lead_id": lead_id, "score": score, "reasons": reasons})

    class FakeAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kw):
            captured_events.append(kw)

    class FakeConfig:
        def __init__(self, session): ...
        async def resolve(self, key, *, merchant_id):
            return {"scoring.hot_threshold": 80, "scoring.cold_threshold": 30}.get(
                getattr(key, "value", str(key))
            )

    monkeypatch.setattr(mod, "tenant_session", fake_session)
    monkeypatch.setattr(mod, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", FakeAnalyticsRepo)
    monkeypatch.setattr(mod, "ConfigResolver", FakeConfig)


def test_derive_signals_whitelists_known_keys() -> None:
    signals = derive_signals_from_llm_payload(
        {"signals": {"has_name": True, "has_budget": True, "unknown_signal": True}}
    )
    assert signals == {"has_name": True, "has_budget": True}


def test_classify_thresholds() -> None:
    assert _classify(85, 80, 30) == "hot"
    assert _classify(50, 80, 30) == "warm"
    assert _classify(20, 80, 30) == "cold"
    assert _classify(80, 80, 30) == "hot"
    assert _classify(30, 80, 30) == "cold"


async def test_handler_persists_score_and_emits_event(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext
) -> None:
    lead = FakeLead(id=turn_ctx.lead_id, score=10)
    events: list = []
    updates: list = []
    _patch(monkeypatch, lead=lead, captured_events=events, captured_updates=updates)

    handler = UpdateScoreHandler()
    await handler(
        OrchestratorAction(
            kind="update_score",
            payload={"signals": {"has_name": True, "has_email": True, "asked_for_booking": True}},
        ),
        turn_ctx,
    )

    assert updates and updates[0]["score"] == 5 + 5 + 20
    assert events and events[0]["event_type"] == "lead_score_changed"
    assert events[0]["properties"]["previous_score"] == 10
    assert events[0]["properties"]["new_score"] == 30


async def test_handler_noops_when_no_signals(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext
) -> None:
    events: list = []
    updates: list = []
    _patch(monkeypatch, lead=None, captured_events=events, captured_updates=updates)

    handler = UpdateScoreHandler()
    await handler(OrchestratorAction(kind="update_score", payload={}), turn_ctx)

    assert updates == []
    assert events == []
