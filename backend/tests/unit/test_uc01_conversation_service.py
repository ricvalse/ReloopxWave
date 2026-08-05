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
    pipeline_stage_id: str | None = None


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
    last_message_at: Any = None
    current_state: str | None = None
    context_summary: dict | None = None
    # Profilo di conversazione attivo (ADR 0022 / migrazione 0047). None =
    # nessun profilo, cioè il comportamento identico a prima dei profili.
    profile_id: uuid.UUID | None = None


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

        async def update_behavioral_signals(self, lead_id, **kw):
            return None

        async def update_intake_score(self, lead_id, **kw):
            return None

    class FakeConvRepo:
        def __init__(self, session): ...
        async def get_active(self, *, merchant_id, wa_contact_phone):
            return None

        async def get_active_or_reopen_latest(self, *, merchant_id, wa_contact_phone):
            return None

        async def create(self, **kw):
            return conv

        async def touch_last_message(self, conversation_id):
            return None

        async def touch_last_inbound(self, conversation_id):
            return None

        async def update_state(self, conversation_id, state):
            return None

        async def save_context_summary(self, conversation_id, summary):
            return None

        async def claim_handoff(self, conversation_id, *, reason=None, summary=None):
            # Mirrors the repo's conditional UPDATE: only the first caller on a
            # bot-owned thread wins the takeover.
            if not conv.auto_reply:
                return False
            conv.auto_reply = False
            conv.handoff_at = datetime.now(UTC)
            conv.handoff_reason = reason
            conv.handoff_resolved_at = None
            return True

    class FakeMsgRepo:
        def __init__(self, session):
            self.user_calls: list = []
            self.assistant_calls: list = []

        async def find_by_wa_message_id(self, wa_message_id):
            return None

        async def list_history(self, conversation_id, *, limit=30):
            return []

        async def resolve_reply_target(self, conversation_id):
            # Nessun tocco precedente in questi test: l'inbound non attribuisce.
            return None

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


def test_trailing_proactive_text_detects_automation_reply() -> None:
    """When the customer's turn replies to a proactive/automation send, the
    automation message text is surfaced so the reply can continue that thread.
    Fires only when the LAST stored turn is proactive (automation/automation_ai);
    once the bot has answered, the customer is replying to the bot."""
    from types import SimpleNamespace

    from ai_core.conversation_service import _trailing_proactive_text

    def msg(role: str, content: str, sender_type: str | None = None):
        meta = {"sender_type": sender_type} if sender_type else {}
        return SimpleNamespace(role=role, content=content, meta=meta)

    # Last turn is the automation send → return its text.
    hist = [msg("agent", "Per completare la candidatura compila il questionario", "automation")]
    assert _trailing_proactive_text(hist) == "Per completare la candidatura compila il questionario"

    # ai_reply node send (automation_ai) also counts as proactive.
    assert (
        _trailing_proactive_text([msg("agent", "Ciao, ci sei?", "automation_ai")])
        == "Ciao, ci sei?"
    )

    # A normal AI reply is NOT proactive continuity context.
    assert _trailing_proactive_text([msg("assistant", "Come posso aiutarti?", "ai")]) is None

    # Bot has already taken over (last turn is an AI reply) → no directive.
    hist2 = [
        msg("agent", "Per completare la candidatura...", "automation"),
        msg("user", "Appena inviato"),
        msg("assistant", "Perfetto, grazie", "ai"),
    ]
    assert _trailing_proactive_text(hist2) is None

    # Empty / plain user tail → None.
    assert _trailing_proactive_text([]) is None
    assert _trailing_proactive_text([msg("user", "Ciao")]) is None


async def test_inbound_persisted_and_fallback_sent_when_llm_fails(
    monkeypatch: pytest.MonkeyPatch,
    resolved_integration: ResolvedWhatsAppIntegration,
) -> None:
    """Fail-safe (QW1): an LLM error must NOT lose the inbound AND must still
    reach the customer.

    The inbound is persisted in its own phase before the reply phase runs, so
    when the orchestrator raises, the user message is already saved. The reply
    phase then catches the error, sends a courtesy fallback, persists it, and
    hands the thread to a human — instead of leaving the customer in silence."""
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
        self,
        *,
        session,
        merchant_id,
        variant_id=None,
        prior_sentiment=None,
        customer_message=None,
        profile_id=None,
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

        async def update_behavioral_signals(self, lead_id, **kw):
            return None

        async def update_intake_score(self, lead_id, **kw):
            return None

    class FakeConvRepo:
        def __init__(self, session): ...
        async def get_active(self, *, merchant_id, wa_contact_phone):
            return conv

        async def get_active_or_reopen_latest(self, *, merchant_id, wa_contact_phone):
            return conv

        async def touch_last_message(self, conversation_id):
            return None

        async def touch_last_inbound(self, conversation_id):
            return None

        async def update_state(self, conversation_id, state):
            return None

        async def save_context_summary(self, conversation_id, summary):
            return None

        async def claim_handoff(self, conversation_id, *, reason=None, summary=None):
            if not conv.auto_reply:
                return False
            conv.auto_reply = False
            conv.handoff_at = datetime.now(UTC)
            conv.handoff_reason = reason
            conv.handoff_resolved_at = None
            return True

    class FakeMsgRepo:
        def __init__(self, session): ...
        async def find_by_wa_message_id(self, wa_message_id):
            return None

        async def list_history(self, conversation_id, *, limit=30):
            return []

        async def resolve_reply_target(self, conversation_id):
            # Nessun tocco precedente in questi test: l'inbound non attribuisce.
            return None

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

    sender = FakeSender()
    svc = ConversationService(
        orchestrator=orch,
        action_dispatcher=ActionDispatcher(),
        reply_sender=sender,
        embedder=None,
        kek_base64="unused",
    )

    from ai_core.conversation_service import _LLM_FAILURE_MESSAGE

    # No exception escapes: the fail-safe path turns the LLM error into a reply.
    result = await svc.handle_inbound(
        phone_number_id="PNID-1",
        from_phone="39333000000",
        text="ciao",
        wa_message_id="wamid.in.999",
    )

    # Inbound was persisted before the reply phase ran.
    assert len(user_calls) == 1
    assert user_calls[0]["wa_message_id"] == "wamid.in.999"
    # The customer got a courtesy fallback (sent + persisted), not silence. The
    # reply may be split across human-feel bubbles on the wire; the persisted
    # assistant Message stays single and authoritative.
    assert result.handled is True
    assert result.reply_text == _LLM_FAILURE_MESSAGE
    assert sender.calls  # at least one bubble went out
    joined = " ".join(c["text"].strip() for c in sender.calls)
    assert joined == _LLM_FAILURE_MESSAGE
    assert len(assistant_calls) == 1
    assert assistant_calls[0]["content"] == _LLM_FAILURE_MESSAGE


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
        self,
        *,
        session,
        merchant_id,
        variant_id=None,
        prior_sentiment=None,
        customer_message=None,
        profile_id=None,
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

        async def update_behavioral_signals(self, lead_id, **kw):
            return None

        async def update_intake_score(self, lead_id, **kw):
            return None

    class FakeConvRepo:
        def __init__(self, session): ...
        async def get_active(self, *, merchant_id, wa_contact_phone):
            return conv

        async def get_active_or_reopen_latest(self, *, merchant_id, wa_contact_phone):
            return conv

        async def touch_last_message(self, conversation_id):
            return None

        async def touch_last_inbound(self, conversation_id):
            return None

        async def update_state(self, conversation_id, state):
            return None

        async def save_context_summary(self, conversation_id, summary):
            return None

        async def claim_handoff(self, conversation_id, *, reason=None, summary=None):
            if not conv.auto_reply:
                return False
            conv.auto_reply = False
            conv.handoff_at = datetime.now(UTC)
            conv.handoff_reason = reason
            conv.handoff_resolved_at = None
            return True

    class FakeMsgRepo:
        def __init__(self, session): ...
        async def find_by_wa_message_id(self, wa_message_id):
            return object()  # already stored

        async def list_history(self, conversation_id, *, limit=30):
            return []

        async def resolve_reply_target(self, conversation_id):
            # Nessun tocco precedente in questi test: l'inbound non attribuisce.
            return None

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


# ---- Handoff exactly-once (regression: 10 foto → 10 messaggi di handoff) ---


def _escalate_response(reply_text: str = "Ti passo un collega.") -> OrchestratorResponse:
    return OrchestratorResponse(
        reply_text=reply_text,
        actions=[
            OrchestratorAction(
                kind="escalate_human",
                payload={"reason": "media", "customer_message_summary": "Ha inviato foto."},
            )
        ],
        model="gpt-5-mini",
        tokens_in=10,
        tokens_out=5,
        latency_ms=10,
    )


async def test_handoff_message_sent_once_for_media_burst(
    monkeypatch: pytest.MonkeyPatch, service
) -> None:
    """Una raffica di foto produce UN solo messaggio di handoff, poi il bot tace.

    Il primo turno che escala vince il claim atomico (auto_reply → False); gli
    inbound successivi trovano il thread in mano all'operatore e vengono solo
    persistiti, senza risposta."""
    from ai_core import conversation_service as cs
    from config_resolver import ConfigKey

    svc, sender, _dispatcher, conv, _lead = service

    async def per_key_bool(self, session, merchant_id, key, *, default):
        return key is not ConfigKey.HANDOFF_SILENT

    monkeypatch.setattr(cs.ConversationService, "_resolve_bool", per_key_bool)

    async def per_key_str(self, session, merchant_id, key):
        if key is ConfigKey.HANDOFF_MESSAGE:
            return "Ti metto in contatto con un operatore."
        return None

    monkeypatch.setattr(cs.ConversationService, "_resolve_optional_str", per_key_str)

    svc._orchestrator.run = AsyncMock(return_value=_escalate_response())

    for i in range(3):
        await svc.handle_inbound(
            phone_number_id="PNID-1",
            from_phone="39333000000",
            text="[Il cliente ha inviato un'immagine]",
            wa_message_id=f"wamid.photo.{i}",
        )

    # Un solo messaggio sul filo: il messaggio di handoff configurato.
    assert len(sender.calls) == 1
    assert sender.calls[0]["text"] == "Ti metto in contatto con un operatore."
    assert conv.auto_reply is False
    assert conv.handoff_at is not None


async def test_lost_handoff_claim_suppresses_reply_and_action(
    monkeypatch: pytest.MonkeyPatch,
    resolved_integration: ResolvedWhatsAppIntegration,
) -> None:
    """Race di turni concorrenti: chi perde il claim non invia nulla, non
    persiste il messaggio assistant e non ri-dispatcha escalate_human (niente
    doppie notifiche all'operatore)."""
    from ai_core import conversation_service as cs

    assistant_calls: list = []
    claim_calls: list = []

    async def fake_resolve(self, phone_number_id):
        return resolved_integration

    async def fake_resolve_int(self, session, merchant_id, key, *, default):
        return 80

    async def fake_resolve_bool(self, session, merchant_id, key, *, default):
        return True

    async def fake_resolve_prompt(
        self,
        *,
        session,
        merchant_id,
        variant_id=None,
        prior_sentiment=None,
        customer_message=None,
        profile_id=None,
    ):
        return "system prompt"

    monkeypatch.setattr(cs.ConversationService, "_resolve_integration", fake_resolve)
    monkeypatch.setattr(cs.ConversationService, "_resolve_int", fake_resolve_int)
    monkeypatch.setattr(cs.ConversationService, "_resolve_bool", fake_resolve_bool)
    monkeypatch.setattr(cs.ConversationService, "_resolve_system_prompt", fake_resolve_prompt)

    lead = FakeLead()
    conv = FakeConversation()  # auto_reply=True: il gate d'ingresso è passato

    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield FakeSession()

    monkeypatch.setattr(cs, "tenant_session", fake_tenant_session)

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def upsert_by_phone(self, *, merchant_id, phone, campaign=None):
            return lead

        async def update_behavioral_signals(self, lead_id, **kw):
            return None

        async def update_intake_score(self, lead_id, **kw):
            return None

    class FakeConvRepo:
        def __init__(self, session): ...
        async def get_active(self, *, merchant_id, wa_contact_phone):
            return conv

        async def get_active_or_reopen_latest(self, *, merchant_id, wa_contact_phone):
            return conv

        async def touch_last_message(self, conversation_id):
            return None

        async def touch_last_inbound(self, conversation_id):
            return None

        async def update_state(self, conversation_id, state):
            return None

        async def save_context_summary(self, conversation_id, summary):
            return None

        async def claim_handoff(self, conversation_id, *, reason=None, summary=None):
            # Un turno concorrente ha già preso il takeover tra il gate e qui.
            claim_calls.append(conversation_id)
            return False

    class FakeMsgRepo:
        def __init__(self, session): ...
        async def find_by_wa_message_id(self, wa_message_id):
            return None

        async def list_history(self, conversation_id, *, limit=30):
            return []

        async def resolve_reply_target(self, conversation_id):
            # Nessun tocco precedente in questi test: l'inbound non attribuisce.
            return None

        async def persist_user_message(self, **kw):
            return None

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
    orch.run = AsyncMock(return_value=_escalate_response())

    dispatcher = ActionDispatcher()
    escalate_dispatched: list = []

    async def spy_escalate(action, ctx):
        escalate_dispatched.append(action)

    dispatcher.register("escalate_human", spy_escalate)

    sender = FakeSender()
    svc = ConversationService(
        orchestrator=orch,
        action_dispatcher=dispatcher,
        reply_sender=sender,
        embedder=None,
        kek_base64="unused",
    )

    result = await svc.handle_inbound(
        phone_number_id="PNID-1",
        from_phone="39333000000",
        text="[Il cliente ha inviato un'immagine]",
        wa_message_id="wamid.race.2",
    )

    assert result.handled is True
    assert claim_calls  # il claim è stato tentato…
    assert sender.calls == []  # …ma perso: niente messaggio al cliente
    assert assistant_calls == []  # niente riga assistant persistita
    assert escalate_dispatched == []  # niente seconda notifica all'operatore


async def test_escalation_disabled_keeps_bot_reply_no_handoff_message(
    monkeypatch: pytest.MonkeyPatch, service
) -> None:
    """Con escalation.enabled=False il thread resta al bot: esce la risposta
    del LLM, NON il messaggio di handoff (che prometterebbe un operatore che
    non arriva, ripetendosi a ogni inbound)."""
    from ai_core import conversation_service as cs
    from config_resolver import ConfigKey

    svc, sender, _dispatcher, conv, _lead = service

    async def per_key_bool(self, session, merchant_id, key, *, default):
        return key not in (ConfigKey.HANDOFF_ENABLED, ConfigKey.HANDOFF_SILENT)

    monkeypatch.setattr(cs.ConversationService, "_resolve_bool", per_key_bool)

    async def per_key_str(self, session, merchant_id, key):
        if key is ConfigKey.HANDOFF_MESSAGE:
            return "Ti metto in contatto con un operatore."
        return None

    monkeypatch.setattr(cs.ConversationService, "_resolve_optional_str", per_key_str)

    svc._orchestrator.run = AsyncMock(
        return_value=_escalate_response(reply_text="Un attimo, verifico e ti aggiorno.")
    )

    await svc.handle_inbound(
        phone_number_id="PNID-1",
        from_phone="39333000000",
        text="[Il cliente ha inviato un'immagine]",
        wa_message_id="wamid.disabled.1",
    )

    assert len(sender.calls) == 1
    assert sender.calls[0]["text"] == "Un attimo, verifico e ti aggiorno."
    # Nessun takeover: il bot resta sul thread.
    assert conv.auto_reply is True
    assert conv.handoff_at is None


async def test_silent_handoff_says_nothing_to_the_customer_but_notifies_the_operator(
    monkeypatch: pytest.MonkeyPatch, service
) -> None:
    """Passaggio silenzioso = silenzioso *verso il cliente*, non verso di noi.

    Il cliente non riceve niente e il bot esce dal thread, ma l'azione
    `escalate_human` viene comunque dispatchata: è lei a emettere
    `conversation.escalated`, cioè l'evento da cui parte la notifica Slack. Se
    tacesse anche quella, l'handoff sarebbe una conversazione abbandonata senza
    che nessuno lo sappia.
    """
    from ai_core import conversation_service as cs
    from config_resolver import ConfigKey

    svc, sender, dispatcher, conv, _lead = service

    async def per_key_bool(self, session, merchant_id, key, *, default):
        if key is ConfigKey.HANDOFF_SILENT:
            return True
        return True

    monkeypatch.setattr(cs.ConversationService, "_resolve_bool", per_key_bool)

    escalations: list = []

    async def spy_escalate(action, ctx):
        escalations.append(ctx)

    dispatcher.register("escalate_human", spy_escalate)

    svc._orchestrator.run = AsyncMock(
        return_value=_escalate_response(reply_text="Ti passo a un operatore.")
    )

    await svc.handle_inbound(
        phone_number_id="PNID-1",
        from_phone="39333000000",
        text="Voglio parlare con una persona",
        wa_message_id="wamid.silent.1",
    )

    assert sender.calls == []  # niente sul filo
    assert conv.auto_reply is False  # e il bot esce dal thread
    assert len(escalations) == 1  # ma l'operatore viene avvisato
    # Il claim è già stato vinto dalla reply-policy: l'handler non deve ritentarlo.
    assert escalations[0].handoff_claimed is True


async def test_configured_handoff_message_wins_over_the_model_text(
    monkeypatch: pytest.MonkeyPatch, service
) -> None:
    """Con il campo «Messaggio di passaggio» compilato esce esattamente quel
    testo, non quello che si è inventato il modello — ed è comunque un handoff
    vero: bot fuori dal thread e operatore notificato."""
    from ai_core import conversation_service as cs
    from config_resolver import ConfigKey

    svc, sender, dispatcher, conv, _lead = service

    async def per_key_bool(self, session, merchant_id, key, *, default):
        # Escalation attiva, ma NON silenziosa: il messaggio deve uscire.
        return key is not ConfigKey.HANDOFF_SILENT

    async def per_key_str(self, session, merchant_id, key):
        if key is ConfigKey.HANDOFF_MESSAGE:
            return "Ti metto subito in contatto con un nostro operatore."
        return None

    monkeypatch.setattr(cs.ConversationService, "_resolve_bool", per_key_bool)
    monkeypatch.setattr(cs.ConversationService, "_resolve_optional_str", per_key_str)

    escalations: list = []

    async def spy_escalate(action, ctx):
        escalations.append(ctx)

    dispatcher.register("escalate_human", spy_escalate)

    svc._orchestrator.run = AsyncMock(
        return_value=_escalate_response(reply_text="Testo improvvisato dal modello.")
    )

    await svc.handle_inbound(
        phone_number_id="PNID-1",
        from_phone="39333000000",
        text="Voglio parlare con una persona",
        wa_message_id="wamid.custom.1",
    )

    assert len(sender.calls) == 1
    assert sender.calls[0]["text"] == "Ti metto subito in contatto con un nostro operatore."
    assert conv.auto_reply is False
    assert len(escalations) == 1


async def test_force_handoff_media_burst_emits_single_escalation_event(
    monkeypatch: pytest.MonkeyPatch, service
) -> None:
    """Una raffica di video/documenti stampa l'handoff e notifica l'operatore
    una volta sola; i file successivi vengono solo persistiti."""
    from ai_core import conversation_service as cs

    events: list = []

    class SharedAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kw):
            events.append(kw)

    monkeypatch.setattr(cs, "AnalyticsRepository", SharedAnalyticsRepo)

    svc, _sender, _dispatcher, conv, _lead = service

    for i in range(3):
        outcome = await svc.handle_inbound_persist(
            phone_number_id="PNID-1",
            from_phone="39333000000",
            text="[Il cliente ha inviato un video]",
            wa_message_id=f"wamid.mediaburst.{i}",
            force_handoff_reason="video_message",
        )
        assert outcome.auto_reply_on is False

    escalated = [e for e in events if e["event_type"] == "conversation.escalated"]
    assert len(escalated) == 1
    assert conv.auto_reply is False
    assert conv.handoff_reason == "video_message"
