"""UC-01 — conversation service flow test.

We stub every external collaborator (DB session, repositories, orchestrator,
WhatsApp sender) to exercise the orchestration logic in isolation. Real-DB
integration tests live under tests/integration/ and are skipped in unit runs.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ai_core.conversation_service import (
    ActionDispatcher,
    ConversationService,
    ReplySender,
)
from ai_core.orchestrator import OrchestratorAction, OrchestratorResponse
from db import ResolvedWhatsAppIntegration

# ---- Fake collaborators ---------------------------------------------------

@dataclass
class FakeLead:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    score: int = 0


@dataclass
class FakeConversation:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    merchant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    variant_id: str | None = None


class FakeSession:
    async def execute(self, *a: Any, **kw: Any) -> Any:
        raise AssertionError("unexpected direct session.execute in UC-01 test")


class FakeSender(ReplySender):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send(
        self,
        *,
        phone_number_id: str,
        to_phone: str,
        text: str,
    ) -> str:
        self.calls.append(
            {"phone_number_id": phone_number_id, "to": to_phone, "text": text}
        )
        return "wamid.fake"


# ---- Test wiring ----------------------------------------------------------

@pytest.fixture
def resolved_integration() -> ResolvedWhatsAppIntegration:
    return ResolvedWhatsAppIntegration(
        merchant_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        phone_number_id="PNID-1",
        meta={"phone_number_id": "PNID-1"},
    )


@pytest.fixture
def orchestrator_response() -> OrchestratorResponse:
    return OrchestratorResponse(
        reply_text="Ciao! Come posso aiutarti?",
        actions=[OrchestratorAction(kind="none")],
        model="gpt-5-mini",
        tokens_in=120,
        tokens_out=18,
        latency_ms=450,
    )


@pytest.fixture
def service(
    monkeypatch: pytest.MonkeyPatch,
    resolved_integration: ResolvedWhatsAppIntegration,
    orchestrator_response: OrchestratorResponse,
) -> tuple[ConversationService, FakeSender, ActionDispatcher, FakeConversation, FakeLead]:
    from ai_core import conversation_service as cs

    # Stub integration resolution.
    async def fake_resolve(self, phone_number_id: str) -> ResolvedWhatsAppIntegration:
        return resolved_integration

    monkeypatch.setattr(cs.ConversationService, "_resolve_integration", fake_resolve)

    # Stub config resolution to a known hot threshold without touching DB.
    async def fake_resolve_int(self, session, merchant_id, key, *, default):
        return 80

    monkeypatch.setattr(cs.ConversationService, "_resolve_int", fake_resolve_int)

    # Stub the orchestrator.
    orch = AsyncMock()
    orch.run = AsyncMock(return_value=orchestrator_response)

    # Stub the action dispatcher so we can assert on calls.
    dispatcher = ActionDispatcher()

    # Stub the tenant_session context manager.
    lead = FakeLead()
    conv = FakeConversation()

    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield FakeSession()

    monkeypatch.setattr(cs, "tenant_session", fake_tenant_session)

    # Stub every repository class at the module level.
    class FakeLeadRepo:
        def __init__(self, session): ...
        async def upsert_by_phone(self, *, merchant_id, phone):
            return lead

    class FakeConvRepo:
        def __init__(self, session): ...
        async def get_active(self, *, merchant_id, wa_contact_phone):
            return None
        async def create(self, **kw):
            return conv
        async def touch_last_message(self, conversation_id):
            return None

    class FakeMsgRepo:
        def __init__(self, session):
            self.user_calls: list = []
            self.assistant_calls: list = []
        async def list_history(self, conversation_id, *, limit=30):
            return []
        async def persist_user_message(self, **kw):
            self.user_calls.append(kw)
        async def persist_assistant_message(self, **kw):
            self.assistant_calls.append(kw)

    class FakeAnalyticsRepo:
        def __init__(self, session):
            self.events: list = []
        async def emit(self, **kw):
            self.events.append(kw)

    monkeypatch.setattr(cs, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(cs, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(cs, "MessageRepository", FakeMsgRepo)
    monkeypatch.setattr(cs, "AnalyticsRepository", FakeAnalyticsRepo)

    sender = FakeSender()
    svc = ConversationService(
        orchestrator=orch,
        action_dispatcher=dispatcher,
        reply_sender=sender,
        embedder=None,
        kek_base64="unused-in-this-test",
    )
    return svc, sender, dispatcher, conv, lead


# ---- Tests ----------------------------------------------------------------

async def test_handle_inbound_sends_reply_and_returns_conversation(
    service,
) -> None:
    svc, sender, _dispatcher, conv, _lead = service

    result = await svc.handle_inbound(
        phone_number_id="PNID-1",
        from_phone="39333000000",
        text="ciao",
        wa_message_id="wamid.in.123",
    )

    assert result.handled is True
    assert result.conversation_id == conv.id
    assert result.reply_text == "Ciao! Come posso aiutarti?"
    assert len(sender.calls) == 1
    assert sender.calls[0]["to"] == "39333000000"
    assert sender.calls[0]["text"].startswith("Ciao")


async def test_handle_inbound_skips_when_integration_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_core import conversation_service as cs

    async def no_integration(self, phone_number_id):
        return None

    monkeypatch.setattr(cs.ConversationService, "_resolve_integration", no_integration)

    svc = ConversationService(
        orchestrator=AsyncMock(),
        action_dispatcher=ActionDispatcher(),
        reply_sender=FakeSender(),
        embedder=None,
        kek_base64="unused",
    )

    result = await svc.handle_inbound(
        phone_number_id="UNKNOWN",
        from_phone="39333000000",
        text="ciao",
        wa_message_id="wamid.x",
    )
    assert result.handled is False
    assert result.reason == "no_integration"


async def test_action_dispatcher_calls_registered_handler(
    service,
) -> None:
    svc, _sender, dispatcher, _conv, _lead = service
    seen: list[OrchestratorAction] = []

    async def handler(action: OrchestratorAction, ctx):
        seen.append(action)

    dispatcher.register("none", handler)

    await svc.handle_inbound(
        phone_number_id="PNID-1",
        from_phone="39333000000",
        text="ciao",
        wa_message_id="wamid.in.456",
    )

    assert len(seen) == 1
    assert seen[0].kind == "none"
