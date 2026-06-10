"""escalate_human action handler unit tests.

Stubs the DB session, config resolver, conversation repo and analytics repo.
Verifies that:
  1. When escalation is enabled, the bot is taken off the thread
     (mark_escalated) and a `conversation.escalated` event is emitted.
  2. When the merchant/agency disabled escalation, nothing happens.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

from ai_core.actions.escalate import EscalateHumanHandler
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
        api_key="test-channel-key",
    )


def _patch(monkeypatch, *, escalation_enabled: bool):
    from ai_core.actions import escalate as mod

    escalated: list[dict] = []
    events: list[dict] = []

    @asynccontextmanager
    async def fake_session(ctx):
        yield object()

    class FakeConfig:
        def __init__(self, session): ...
        async def resolve(self, key, *, merchant_id):
            return escalation_enabled

    class FakeConvRepo:
        def __init__(self, session): ...
        async def mark_escalated(self, conversation_id, *, reason=None):
            escalated.append({"conversation_id": conversation_id, "reason": reason})

    class FakeAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kw):
            events.append(kw)

    monkeypatch.setattr(mod, "tenant_session", fake_session)
    monkeypatch.setattr(mod, "ConfigResolver", FakeConfig)
    monkeypatch.setattr(mod, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", FakeAnalyticsRepo)
    return escalated, events


async def test_escalate_takes_bot_off_thread_and_emits(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext
) -> None:
    escalated, events = _patch(monkeypatch, escalation_enabled=True)

    handler = EscalateHumanHandler()
    await handler(
        OrchestratorAction(kind="escalate_human", payload={"reason": "angry"}),
        turn_ctx,
    )

    assert len(escalated) == 1
    assert escalated[0]["conversation_id"] == turn_ctx.conversation_id
    assert escalated[0]["reason"] == "angry"
    assert len(events) == 1
    assert events[0]["event_type"] == "conversation.escalated"
    assert events[0]["subject_id"] == turn_ctx.conversation_id


async def test_escalate_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext
) -> None:
    escalated, events = _patch(monkeypatch, escalation_enabled=False)

    handler = EscalateHumanHandler()
    await handler(OrchestratorAction(kind="escalate_human", payload={}), turn_ctx)

    assert escalated == []
    assert events == []
