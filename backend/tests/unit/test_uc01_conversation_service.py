"""UC-01 — conversation service flow test.

We stub every external collaborator (DB session, repositories, orchestrator,
WhatsApp sender) to exercise the orchestration logic in isolation. Real-DB
integration tests live under tests/integration/ and are skipped in unit runs.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ai_core.conversation_service import (
    ActionDispatcher,
    ConversationService,
    ReplySender,
    _to_chat_history,
)
from ai_core.orchestrator import OrchestratorAction, OrchestratorResponse
from db import ResolvedWhatsAppIntegration

# ---- Fake collaborators ---------------------------------------------------


@dataclass
class FakeLead:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    score: int = 0
    name: str | None = None
    email: str | None = None
    sentiment: str | None = None


@dataclass
class FakeConversation:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    merchant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    variant_id: str | None = None
    auto_reply: bool = True
    ai_disabled_until: Any = None
    handoff_at: Any = None
    handoff_reason: str | None = None
    handoff_resolved_at: Any = None


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
        api_key: str,
        to_phone: str,
        text: str,
        waba_base_url: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "phone_number_id": phone_number_id,
                "api_key": api_key,
                "to": to_phone,
                "text": text,
                "waba_base_url": waba_base_url,
            }
        )
        return "wamid.fake"


# ---- Test wiring ----------------------------------------------------------


@pytest.fixture
def resolved_integration() -> ResolvedWhatsAppIntegration:
    return ResolvedWhatsAppIntegration(
        merchant_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        phone_number_id="PNID-1",
        api_key="test-channel-key",
        waba_base_url=None,
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

    # Auto-reply now defaults OFF (master kill switch). These tests exercise the
    # reply path, so explicitly turn it on at the merchant level.
    async def fake_resolve_bool(self, session, merchant_id, key, *, default):
        return True

    monkeypatch.setattr(cs.ConversationService, "_resolve_bool", fake_resolve_bool)

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
        async def upsert_by_phone(self, *, merchant_id, phone, campaign=None):
            return lead

    class FakeConvRepo:
        def __init__(self, session): ...
        async def get_active(self, *, merchant_id, wa_contact_phone):
            return None

        async def create(self, **kw):
            return conv

        async def touch_last_message(self, conversation_id):
            return None

        async def touch_last_inbound(self, conversation_id):
            return None

    class FakeMsgRepo:
        def __init__(self, session):
            self.user_calls: list = []
            self.assistant_calls: list = []

        async def find_by_wa_message_id(self, wa_message_id):
            return None

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


@dataclass
class _StoredMsg:
    role: str
    content: str


def test_to_chat_history_folds_agent_into_assistant() -> None:
    """`agent` (human composer / phone echo) must map to `assistant` so the
    OpenAI payload stays within the accepted role set; other roles pass through."""
    history = [
        _StoredMsg(role="user", content="Ciao"),
        _StoredMsg(role="assistant", content="Come posso aiutarti?"),
        _StoredMsg(role="agent", content="Rispondo io dal telefono"),
        _StoredMsg(role="system", content="ctx"),
    ]

    out = _to_chat_history(history)

    assert [m.role for m in out] == ["user", "assistant", "assistant", "system"]
    assert out[2].content == "Rispondo io dal telefono"


async def test_inbound_persisted_even_when_reply_fails(
    monkeypatch: pytest.MonkeyPatch,
    resolved_integration: ResolvedWhatsAppIntegration,
) -> None:
    """Durability: a failure during reply generation must NOT lose the inbound.

    The inbound is persisted in its own phase that completes before the reply
    phase runs, so when the orchestrator raises, the user message is already
    saved and no assistant message is written."""
    from ai_core import conversation_service as cs

    user_calls: list = []
    assistant_calls: list = []

    async def fake_resolve(self, phone_number_id):
        return resolved_integration

    async def fake_resolve_int(self, session, merchant_id, key, *, default):
        return 80

    async def fake_resolve_bool(self, session, merchant_id, key, *, default):
        return True

    async def fake_resolve_prompt(
        self, *, session, merchant_id, variant_id=None, prior_sentiment=None, customer_message=None
    ):
        return "system prompt"

    monkeypatch.setattr(cs.ConversationService, "_resolve_integration", fake_resolve)
    monkeypatch.setattr(cs.ConversationService, "_resolve_int", fake_resolve_int)
    monkeypatch.setattr(cs.ConversationService, "_resolve_bool", fake_resolve_bool)
    monkeypatch.setattr(cs.ConversationService, "_resolve_system_prompt", fake_resolve_prompt)

    lead = FakeLead()
    conv = FakeConversation()

    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield FakeSession()

    monkeypatch.setattr(cs, "tenant_session", fake_tenant_session)

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def upsert_by_phone(self, *, merchant_id, phone, campaign=None):
            return lead

    class FakeConvRepo:
        def __init__(self, session): ...
        async def get_active(self, *, merchant_id, wa_contact_phone):
            return conv

        async def touch_last_message(self, conversation_id):
            return None

        async def touch_last_inbound(self, conversation_id):
            return None

    class FakeMsgRepo:
        def __init__(self, session): ...
        async def find_by_wa_message_id(self, wa_message_id):
            return None

        async def list_history(self, conversation_id, *, limit=30):
            return []

        async def persist_user_message(self, **kw):
            user_calls.append(kw)

        async def persist_assistant_message(self, **kw):
            assistant_calls.append(kw)

    class FakeAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kw):
            return None

    monkeypatch.setattr(cs, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(cs, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(cs, "MessageRepository", FakeMsgRepo)
    monkeypatch.setattr(cs, "AnalyticsRepository", FakeAnalyticsRepo)

    orch = AsyncMock()
    orch.run = AsyncMock(side_effect=RuntimeError("LLM blew up"))

    svc = ConversationService(
        orchestrator=orch,
        action_dispatcher=ActionDispatcher(),
        reply_sender=FakeSender(),
        embedder=None,
        kek_base64="unused",
    )

    with pytest.raises(RuntimeError):
        await svc.handle_inbound(
            phone_number_id="PNID-1",
            from_phone="39333000000",
            text="ciao",
            wa_message_id="wamid.in.999",
        )

    # Inbound was persisted before the reply phase failed; reply was not.
    assert len(user_calls) == 1
    assert user_calls[0]["wa_message_id"] == "wamid.in.999"
    assert assistant_calls == []


async def test_inbound_idempotent_on_redelivery(
    monkeypatch: pytest.MonkeyPatch,
    resolved_integration: ResolvedWhatsAppIntegration,
    orchestrator_response: OrchestratorResponse,
) -> None:
    """A redelivered webhook (wa_message_id already stored) must not re-insert
    the inbound, but should still produce a reply."""
    from ai_core import conversation_service as cs

    user_calls: list = []

    async def fake_resolve(self, phone_number_id):
        return resolved_integration

    async def fake_resolve_int(self, session, merchant_id, key, *, default):
        return 80

    async def fake_resolve_bool(self, session, merchant_id, key, *, default):
        return True

    async def fake_resolve_prompt(
        self, *, session, merchant_id, variant_id=None, prior_sentiment=None, customer_message=None
    ):
        return "system prompt"

    monkeypatch.setattr(cs.ConversationService, "_resolve_integration", fake_resolve)
    monkeypatch.setattr(cs.ConversationService, "_resolve_int", fake_resolve_int)
    monkeypatch.setattr(cs.ConversationService, "_resolve_bool", fake_resolve_bool)
    monkeypatch.setattr(cs.ConversationService, "_resolve_system_prompt", fake_resolve_prompt)

    conv = FakeConversation()

    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield FakeSession()

    monkeypatch.setattr(cs, "tenant_session", fake_tenant_session)

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def upsert_by_phone(self, *, merchant_id, phone, campaign=None):
            return FakeLead()

    class FakeConvRepo:
        def __init__(self, session): ...
        async def get_active(self, *, merchant_id, wa_contact_phone):
            return conv

        async def touch_last_message(self, conversation_id):
            return None

        async def touch_last_inbound(self, conversation_id):
            return None

    class FakeMsgRepo:
        def __init__(self, session): ...
        async def find_by_wa_message_id(self, wa_message_id):
            return object()  # already stored

        async def list_history(self, conversation_id, *, limit=30):
            return []

        async def persist_user_message(self, **kw):
            user_calls.append(kw)

        async def persist_assistant_message(self, **kw):
            return None

    class FakeAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kw):
            return None

    monkeypatch.setattr(cs, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(cs, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(cs, "MessageRepository", FakeMsgRepo)
    monkeypatch.setattr(cs, "AnalyticsRepository", FakeAnalyticsRepo)

    orch = AsyncMock()
    orch.run = AsyncMock(return_value=orchestrator_response)

    svc = ConversationService(
        orchestrator=orch,
        action_dispatcher=ActionDispatcher(),
        reply_sender=FakeSender(),
        embedder=None,
        kek_base64="unused",
    )

    result = await svc.handle_inbound(
        phone_number_id="PNID-1",
        from_phone="39333000000",
        text="ciao",
        wa_message_id="wamid.dup.1",
    )

    assert result.handled is True
    assert user_calls == []  # no re-insert


async def test_soft_pause_silences_bot(service) -> None:
    """A future `ai_disabled_until` (soft-pause) gates auto-reply off without
    flipping auto_reply — the bot resumes on its own when the window elapses."""
    svc, _sender, _dispatcher, conv, _lead = service
    conv.ai_disabled_until = datetime.now(UTC) + timedelta(hours=1)

    outcome = await svc.handle_inbound_persist(
        phone_number_id="PNID-1",
        from_phone="39333000000",
        text="ci sei?",
        wa_message_id="wamid.pause.1",
    )

    assert outcome.handled is True
    assert outcome.auto_reply_on is False
    # Soft-pause does NOT flip the per-thread takeover flag.
    assert conv.auto_reply is True


async def test_expired_soft_pause_lets_bot_reply(service) -> None:
    """A past `ai_disabled_until` no longer pauses — the bot is back on."""
    svc, _sender, _dispatcher, conv, _lead = service
    conv.ai_disabled_until = datetime.now(UTC) - timedelta(minutes=1)

    outcome = await svc.handle_inbound_persist(
        phone_number_id="PNID-1",
        from_phone="39333000000",
        text="ciao",
        wa_message_id="wamid.pause.2",
    )

    assert outcome.auto_reply_on is True


async def test_force_handoff_media_marks_needs_human(service) -> None:
    """Unsupported media (video/document) hands the thread to a human: persist
    the inbound, flip needs-human, and skip the reply."""
    svc, _sender, _dispatcher, conv, _lead = service

    outcome = await svc.handle_inbound_persist(
        phone_number_id="PNID-1",
        from_phone="39333000000",
        text="[Il cliente ha inviato un video]",
        wa_message_id="wamid.media.1",
        force_handoff_reason="video_message",
    )

    assert outcome.handled is True
    assert outcome.auto_reply_on is False
    assert conv.auto_reply is False
    assert conv.handoff_reason == "video_message"
    assert conv.handoff_at is not None
