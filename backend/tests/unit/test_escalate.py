"""escalate_human action handler unit tests.

Stubs the DB session, config resolver, conversation repo and analytics repo.
Verifies that:
  1. When escalation is enabled and no reply policy claimed ahead of the handler
     (the proactive `ai_reply` path), the handler takes the atomic claim itself
     and emits `conversation.escalated`.
  2. Losing that claim means someone else already handed the thread off: no
     event, so the operator isn't notified twice for one handoff (ADR 0017).
  3. When the caller already won the claim (`turn_ctx.handoff_claimed`, the
     inbound path), the handler does not claim again — it only records.
  4. When the merchant/agency disabled escalation, nothing happens.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import replace

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


def _patch(monkeypatch, *, escalation_enabled: bool, claim_wins: bool = True):
    from ai_core.actions import escalate as mod

    claims: list[dict] = []
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
        async def claim_handoff(self, conversation_id, *, reason=None, summary=None):
            claims.append(
                {"conversation_id": conversation_id, "reason": reason, "summary": summary}
            )
            return claim_wins

    class FakeAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kw):
            events.append(kw)

    monkeypatch.setattr(mod, "tenant_session", fake_session)
    monkeypatch.setattr(mod, "ConfigResolver", FakeConfig)
    monkeypatch.setattr(mod, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", FakeAnalyticsRepo)
    return claims, events


async def test_escalate_takes_bot_off_thread_and_emits(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext
) -> None:
    claims, events = _patch(monkeypatch, escalation_enabled=True)

    handler = EscalateHumanHandler()
    await handler(
        OrchestratorAction(
            kind="escalate_human",
            payload={
                "reason": "angry",
                "customer_message_summary": "Vuole un rimborso, è insoddisfatto.",
            },
        ),
        turn_ctx,
    )

    assert len(claims) == 1
    assert claims[0]["conversation_id"] == turn_ctx.conversation_id
    assert claims[0]["reason"] == "angry"
    # The AI's operator brief flows through to the handoff_summary column.
    assert claims[0]["summary"] == "Vuole un rimborso, è insoddisfatto."
    assert len(events) == 1
    assert events[0]["event_type"] == "conversation.escalated"
    assert events[0]["subject_id"] == turn_ctx.conversation_id
    assert events[0]["properties"]["summary"] == "Vuole un rimborso, è insoddisfatto."


async def test_escalate_losing_the_claim_emits_nothing(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext
) -> None:
    """Concurrent proactive turns: the thread is already handed off, so this one
    must not re-stamp the episode nor fire a second operator notification."""
    claims, events = _patch(monkeypatch, escalation_enabled=True, claim_wins=False)

    handler = EscalateHumanHandler()
    await handler(OrchestratorAction(kind="escalate_human", payload={"reason": "angry"}), turn_ctx)

    assert len(claims) == 1  # tried
    assert events == []  # and stayed quiet


async def test_escalate_does_not_reclaim_when_caller_already_won(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext
) -> None:
    """Inbound path: the reply policy claimed before sending the handoff message.
    Claiming again here would fail (`auto_reply` is already false) and swallow the
    event for a handoff that genuinely just happened."""
    claims, events = _patch(monkeypatch, escalation_enabled=True, claim_wins=False)

    handler = EscalateHumanHandler()
    await handler(
        OrchestratorAction(kind="escalate_human", payload={"reason": "angry"}),
        replace(turn_ctx, handoff_claimed=True),
    )

    assert claims == []
    assert len(events) == 1
    assert events[0]["event_type"] == "conversation.escalated"


async def test_escalate_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext
) -> None:
    claims, events = _patch(monkeypatch, escalation_enabled=False)

    handler = EscalateHumanHandler()
    await handler(OrchestratorAction(kind="escalate_human", payload={}), turn_ctx)

    assert claims == []
    assert events == []


async def test_disabled_escalation_still_notifies_a_takeover_that_happened(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext
) -> None:
    """Hard LLM failure with escalation locked off: the reply policy claims anyway
    as a safety net. Staying quiet here would leave a permanently silent thread,
    a customer promised an operator, and nobody alerted."""
    claims, events = _patch(monkeypatch, escalation_enabled=False)

    handler = EscalateHumanHandler()
    await handler(
        OrchestratorAction(kind="escalate_human", payload={"reason": "ai_error"}),
        replace(turn_ctx, handoff_claimed=True),
    )

    assert claims == []  # the caller already claimed
    assert len(events) == 1
    assert events[0]["properties"]["reason"] == "ai_error"
