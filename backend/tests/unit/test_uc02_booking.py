"""UC-02 — book_slot action handler unit tests.

Stubs GHLClient, DB session, and the reply sender. Verifies that:
  1. Happy path: GHL.create_booking succeeds → booking confirmation sent.
  2. Slot taken: integration error → alternatives proposed to the lead.
  3. No GHL integration: we report the reason, do not crash, send a graceful message.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_core.actions.booking import BookSlotHandler, ProposeSlotsHandler, _verified_free_slots
from ai_core.conversation_service import TurnContext
from ai_core.orchestrator import OrchestratorAction
from db import ResolvedGHLIntegration
from shared import IntegrationError


@dataclass
class FakeSender:
    calls: list[dict] = field(default_factory=list)

    async def send(self, *, phone_number_id, api_key, to_phone, text, waba_base_url=None):
        self.calls.append({"to": to_phone, "text": text})
        return "wamid.confirm"


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


@pytest.fixture
def ghl_bundle(turn_ctx: TurnContext) -> ResolvedGHLIntegration:
    return ResolvedGHLIntegration(
        merchant_id=turn_ctx.merchant_id,
        tenant_id=turn_ctx.tenant_id,
        access_token="ghl-at",
        refresh_token="ghl-rt",
        expires_at=0,
        location_id="loc-1",
        meta={},
    )


def _patch_session(monkeypatch, *, ghl: ResolvedGHLIntegration | None) -> list[dict]:
    """Replace the action-handler's DB touchpoints with fakes.

    Returns the list that records `AppointmentRepository.record_booking` calls so
    tests can assert the write-through mirror fired (UC-02).
    """
    from ai_core.actions import booking as mod

    class _FakeSession:
        @asynccontextmanager
        async def begin_nested(self):
            yield

    @asynccontextmanager
    async def fake_session(ctx):
        yield _FakeSession()

    appt_calls: list[dict] = []

    class FakeAppointmentRepo:
        def __init__(self, session): ...
        async def record_booking(self, **kw):
            appt_calls.append(kw)

    class FakeIntegrationRepo:
        def __init__(self, session, *, kek_base64): ...
        async def resolve_ghl(self, merchant_id):
            return ghl

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def update_score(self, lead_id, *, score, reasons): ...
        async def get_by_phone(self, *, merchant_id, phone):
            return None

        async def update_contact_fields(self, lead_id, *, name=None, email=None): ...

    class FakeAnalyticsRepo:
        def __init__(self, session):
            self.events: list[dict] = []

        async def emit(self, **kw):
            self.events.append(kw)

    class FakeConfig:
        def __init__(self, session): ...
        async def resolve(self, key, *, merchant_id):
            return {
                "booking.default_calendar_id": "CAL-1",
                "booking.default_duration_min": 30,
            }.get(getattr(key, "value", str(key)))

    class FakeAutomationRepo:
        # No enabled booking_created automation → reminder hours come from config.
        def __init__(self, session): ...
        async def list_enabled_by_trigger(self, *, merchant_id, trigger_type):
            return []

    monkeypatch.setattr(mod, "tenant_session", fake_session)
    monkeypatch.setattr(mod, "IntegrationRepository", FakeIntegrationRepo)
    monkeypatch.setattr(mod, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", FakeAnalyticsRepo)
    monkeypatch.setattr(mod, "AppointmentRepository", FakeAppointmentRepo)
    monkeypatch.setattr(mod, "AutomationRepository", FakeAutomationRepo)
    monkeypatch.setattr(mod, "ConfigResolver", FakeConfig)
    return appt_calls


def _patch_ghl_client(monkeypatch, *, booking_ok: bool):
    from ai_core.actions import booking as mod

    client = AsyncMock()
    client.upsert_contact = AsyncMock(return_value={"contact": {"id": "CT-1"}})
    if booking_ok:
        client.create_booking = AsyncMock(return_value={"id": "BK-1"})
    else:
        client.create_booking = AsyncMock(
            side_effect=IntegrationError("slot taken", error_code="ghl_request_failed")
        )
    client.get_free_slots = AsyncMock(
        return_value=[
            {"startTime": "2026-04-25T09:00:00+02:00"},
            {"startTime": "2026-04-25T10:00:00+02:00"},
            {"startTime": "2026-04-25T11:00:00+02:00"},
        ]
    )
    client.close = AsyncMock()

    def _ctor(**_: Any):
        return client

    monkeypatch.setattr(mod, "GHLClient", MagicMock(side_effect=lambda **kw: client))
    return client


# ---- tests ---------------------------------------------------------------


async def test_book_slot_happy_path(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext, ghl_bundle: ResolvedGHLIntegration
) -> None:
    appt_calls = _patch_session(monkeypatch, ghl=ghl_bundle)
    ghl_client = _patch_ghl_client(monkeypatch, booking_ok=True)
    sender = FakeSender()

    handler = BookSlotHandler(
        kek_base64="unused",
        ghl_client_id="x",
        ghl_client_secret="y",
        reply_sender=sender,
    )

    await handler(
        OrchestratorAction(
            kind="book_slot",
            payload={
                "preferred_start_iso": "2026-04-25T10:00:00+02:00",
                "contact_fields": {"name": "Mario", "email": "m@example.com"},
            },
        ),
        turn_ctx,
    )

    ghl_client.upsert_contact.assert_awaited_once()
    ghl_client.create_booking.assert_awaited_once()
    assert len(sender.calls) == 1
    assert "prenotato" in sender.calls[0]["text"]

    # Write-through mirror: the GHL appointment_id (otherwise dropped) is
    # persisted locally with the resolved slot window and contact handle.
    assert len(appt_calls) == 1
    mirrored = appt_calls[0]
    assert mirrored["ghl_appointment_id"] == "BK-1"
    assert mirrored["ghl_contact_id"] == "CT-1"
    assert mirrored["calendar_id"] == "CAL-1"
    assert mirrored["start_at"].isoformat() == "2026-04-25T10:00:00+02:00"
    assert mirrored["end_at"].isoformat() == "2026-04-25T10:30:00+02:00"


async def test_book_slot_taken_proposes_alternatives(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext, ghl_bundle: ResolvedGHLIntegration
) -> None:
    appt_calls = _patch_session(monkeypatch, ghl=ghl_bundle)
    ghl_client = _patch_ghl_client(monkeypatch, booking_ok=False)
    sender = FakeSender()

    handler = BookSlotHandler(
        kek_base64="unused",
        ghl_client_id="x",
        ghl_client_secret="y",
        reply_sender=sender,
    )

    await handler(
        OrchestratorAction(
            kind="book_slot",
            payload={"preferred_start_iso": "2026-04-25T10:00:00+02:00"},
        ),
        turn_ctx,
    )

    ghl_client.get_free_slots.assert_awaited_once()
    assert len(sender.calls) == 1
    assert "non è più disponibile" in sender.calls[0]["text"]
    # The fixture's free-slots response includes the exact 10:00 slot that was
    # just rejected as taken (reproduces a prod bug: GHL's free-slots read and
    # its booking write disagreed, so the "alternative" kept re-offering the
    # very instant that had just failed, looping the customer forever). The
    # handler must drop it — 3 raw slots minus the rejected one = 2 bullets.
    assert sender.calls[0]["text"].count("•") == 2
    assert "10:00" not in sender.calls[0]["text"], "the rejected slot must not reappear"
    # No booking → nothing mirrored locally.
    assert appt_calls == []


async def test_book_slot_taken_uses_configured_lookahead_window(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext, ghl_bundle: ResolvedGHLIntegration
) -> None:
    # #2 — the alternatives window must come from booking.lookahead_days, not a
    # hardcoded 3 days. With lookahead=7 and slot_start 2026-04-25T10:00+02:00
    # the proposed-slots query end must be 2026-05-02T10:00+02:00.
    _patch_session(monkeypatch, ghl=ghl_bundle)
    from ai_core.actions import booking as mod

    class LookaheadConfig:
        def __init__(self, session): ...
        async def resolve(self, key, *, merchant_id):
            return {
                "booking.default_calendar_id": "CAL-1",
                "booking.default_duration_min": 30,
                "booking.lookahead_days": 7,
            }.get(getattr(key, "value", str(key)))

    monkeypatch.setattr(mod, "ConfigResolver", LookaheadConfig)
    ghl_client = _patch_ghl_client(monkeypatch, booking_ok=False)
    sender = FakeSender()

    handler = BookSlotHandler(
        kek_base64="unused",
        ghl_client_id="x",
        ghl_client_secret="y",
        reply_sender=sender,
    )
    await handler(
        OrchestratorAction(
            kind="book_slot",
            payload={"preferred_start_iso": "2026-04-25T10:00:00+02:00"},
        ),
        turn_ctx,
    )

    ghl_client.get_free_slots.assert_awaited_once()
    end_iso = ghl_client.get_free_slots.await_args.kwargs["end_iso"]
    assert end_iso == "2026-05-02T10:00:00+02:00"


async def test_propose_slots_offers_availability(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext, ghl_bundle: ResolvedGHLIntegration
) -> None:
    _patch_session(monkeypatch, ghl=ghl_bundle)
    from ai_core.actions import booking as mod

    client = AsyncMock()
    client.get_free_slots = AsyncMock(
        return_value=[
            {"startTime": "2026-04-25T09:00:00+02:00"},
            {"startTime": "2026-04-25T10:00:00+02:00"},
            {"startTime": "2026-04-25T11:00:00+02:00"},
        ]
    )
    client.close = AsyncMock()
    monkeypatch.setattr(mod, "GHLClient", MagicMock(side_effect=lambda **kw: client))

    sender = FakeSender()
    handler = ProposeSlotsHandler(
        kek_base64="unused", ghl_client_id="x", ghl_client_secret="y", reply_sender=sender
    )
    await handler(OrchestratorAction(kind="propose_slots", payload={}), turn_ctx)

    client.get_free_slots.assert_awaited_once()
    assert len(sender.calls) == 1
    assert sender.calls[0]["text"].count("•") == 3
    assert "disponibilità" in sender.calls[0]["text"].lower()


async def test_book_slot_transient_error_falls_back_to_internal_calendar(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext, ghl_bundle: ResolvedGHLIntegration
) -> None:
    # A 5xx from create_booking is transient — we must NOT propose alternatives
    # and must save the appointment in the internal calendar as fallback.
    appt_calls = _patch_session(monkeypatch, ghl=ghl_bundle)
    from ai_core.actions import booking as mod

    client = AsyncMock()
    client.upsert_contact = AsyncMock(return_value={"contact": {"id": "CT-1"}})
    client.create_booking = AsyncMock(
        side_effect=IntegrationError("ghl down", error_code="ghl_request_failed", status=503)
    )
    client.get_free_slots = AsyncMock()
    client.close = AsyncMock()
    monkeypatch.setattr(mod, "GHLClient", MagicMock(side_effect=lambda **kw: client))

    sender = FakeSender()
    handler = BookSlotHandler(
        kek_base64="unused", ghl_client_id="x", ghl_client_secret="y", reply_sender=sender
    )
    await handler(
        OrchestratorAction(
            kind="book_slot", payload={"preferred_start_iso": "2026-07-15T15:00:00"}
        ),
        turn_ctx,
    )

    client.get_free_slots.assert_not_awaited()  # no misleading alternatives
    assert len(sender.calls) == 1
    # Internal calendar fallback → conferma immediata, senza "operatore"
    assert "prenotato" in sender.calls[0]["text"].lower()
    assert "operatore" not in sender.calls[0]["text"].lower()
    assert "•" not in sender.calls[0]["text"]
    # Must have written to the internal calendar
    assert len(appt_calls) == 1
    assert appt_calls[0]["ghl_appointment_id"] is None  # local-only row


async def test_book_slot_naive_time_interpreted_in_merchant_tz(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext, ghl_bundle: ResolvedGHLIntegration
) -> None:
    """A naïve ISO (no offset — what the LLM usually emits) must be booked in the
    merchant's local timezone, not UTC. For Europe/Rome in July that is +02:00."""
    _patch_session(monkeypatch, ghl=ghl_bundle)
    ghl_client = _patch_ghl_client(monkeypatch, booking_ok=True)
    sender = FakeSender()

    handler = BookSlotHandler(
        kek_base64="unused",
        ghl_client_id="x",
        ghl_client_secret="y",
        reply_sender=sender,
    )

    await handler(
        OrchestratorAction(
            kind="book_slot",
            payload={"preferred_start_iso": "2026-07-15T15:00:00"},
        ),
        turn_ctx,
    )

    ghl_client.create_booking.assert_awaited_once()
    kwargs = ghl_client.create_booking.await_args.kwargs
    assert kwargs["slot_start_iso"] == "2026-07-15T15:00:00+02:00"
    # 30-min default duration, same offset preserved.
    assert kwargs["slot_end_iso"] == "2026-07-15T15:30:00+02:00"


async def test_book_slot_no_ghl_saves_local_appointment(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext
) -> None:
    """Senza GHL: l'appuntamento viene salvato localmente e confermato immediatamente."""
    appt_calls = _patch_session(monkeypatch, ghl=None)
    sender = FakeSender()

    handler = BookSlotHandler(
        kek_base64="unused",
        ghl_client_id="x",
        ghl_client_secret="y",
        reply_sender=sender,
    )

    await handler(OrchestratorAction(kind="book_slot", payload={}), turn_ctx)

    # Il messaggio deve confermare la prenotazione direttamente, senza "operatore".
    assert len(sender.calls) == 1
    assert "prenotato" in sender.calls[0]["text"].lower()
    assert "operatore" not in sender.calls[0]["text"]
    assert "non riesco" not in sender.calls[0]["text"]

    # Il record locale deve essere booked (status canonico del mirror) e senza
    # ghl_appointment_id. "booked" è l'unico stato attivo riconosciuto dai
    # contatori agenda e dalla normalizzazione del reconcile poll.
    assert len(appt_calls) == 1
    assert appt_calls[0]["ghl_appointment_id"] is None
    assert appt_calls[0]["source"] == "bot_local"
    assert appt_calls[0]["status"] == "booked"


def _patch_services(monkeypatch, *, services: list, resolve: dict):
    """Patch ServiceRepository so the booking gate sees a real service catalog.

    `services` is the list returned by `.list()` (the configured menu); `resolve`
    maps service_id (str) → service object returned by `.get()`."""
    from ai_core.actions import booking as mod

    class FakeServiceRepo:
        def __init__(self, session): ...

        async def list(self, merchant_id, *, include_inactive: bool = False):
            return list(services)

        async def get(self, merchant_id, service_id):
            return resolve.get(str(service_id))

    monkeypatch.setattr(mod, "ServiceRepository", FakeServiceRepo)


@dataclass
class _FakeService:
    id: uuid.UUID
    name: str
    duration_min: int = 30
    is_active: bool = True
    ghl_calendar_id: str | None = None


async def test_book_slot_requires_valid_service_when_catalog_configured(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext, ghl_bundle: ResolvedGHLIntegration
) -> None:
    """Gate UC-02: il merchant ha un catalogo servizi ma l'agente non indica un
    service_id valido → NESSUNA prenotazione, si chiede al lead quale servizio."""
    appt_calls = _patch_session(monkeypatch, ghl=ghl_bundle)
    ghl_client = _patch_ghl_client(monkeypatch, booking_ok=True)
    _patch_services(
        monkeypatch,
        services=[
            _FakeService(id=uuid.uuid4(), name="Taglio"),
            _FakeService(id=uuid.uuid4(), name="Colore"),
        ],
        resolve={},  # qualunque service_id non risolve
    )
    sender = FakeSender()
    handler = BookSlotHandler(
        kek_base64="unused", ghl_client_id="x", ghl_client_secret="y", reply_sender=sender
    )

    await handler(
        OrchestratorAction(
            kind="book_slot",
            payload={"preferred_start_iso": "2026-04-25T10:00:00+02:00"},
        ),
        turn_ctx,
    )

    # Nessuna scrittura su GHL né mirror locale: l'appuntamento non esiste.
    ghl_client.create_booking.assert_not_awaited()
    assert appt_calls == []
    # Il lead riceve l'elenco dei servizi da scegliere.
    assert len(sender.calls) == 1
    text = sender.calls[0]["text"]
    assert "servizio" in text.lower()
    assert "Taglio" in text and "Colore" in text


async def test_book_slot_with_valid_service_uses_its_duration(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext, ghl_bundle: ResolvedGHLIntegration
) -> None:
    """Con un service_id valido la prenotazione procede usando la durata del
    servizio (45 min), che sovrascrive il default di 30 min."""
    _patch_session(monkeypatch, ghl=ghl_bundle)
    ghl_client = _patch_ghl_client(monkeypatch, booking_ok=True)
    svc = _FakeService(id=uuid.uuid4(), name="Consulenza", duration_min=45)
    _patch_services(monkeypatch, services=[svc], resolve={str(svc.id): svc})
    sender = FakeSender()
    handler = BookSlotHandler(
        kek_base64="unused", ghl_client_id="x", ghl_client_secret="y", reply_sender=sender
    )

    await handler(
        OrchestratorAction(
            kind="book_slot",
            payload={
                "preferred_start_iso": "2026-04-25T10:00:00+02:00",
                "service_id": str(svc.id),
            },
        ),
        turn_ctx,
    )

    ghl_client.create_booking.assert_awaited_once()
    kwargs = ghl_client.create_booking.await_args.kwargs
    assert kwargs["slot_start_iso"] == "2026-04-25T10:00:00+02:00"
    assert kwargs["slot_end_iso"] == "2026-04-25T10:45:00+02:00"


# ---- ADR 0011: reminder hours-before sourced from the canvas ----------------


async def test_booking_reminder_hours_from_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """An enabled booking_reminder system flow drives the reminder anticipi from
    its `wait_until_before` nodes (largest-first), overriding the config schedule."""
    from types import SimpleNamespace

    from ai_core.actions import booking as mod

    def _node(node_key, kind, ntype, **cfg):
        return SimpleNamespace(node_key=node_key, kind=kind, type=ntype, config=cfg)

    def _edge(s, t):
        return SimpleNamespace(source_key=s, target_key=t, branch="default")

    flow = SimpleNamespace(
        enabled=True,
        nodes=[
            _node("t", "trigger", "booking_created"),
            _node("wb1", "action", "wait_until_before", hours=48),
            _node("s1", "action", "send"),
            _node("wb2", "action", "wait_until_before", hours=2),
            _node("s2", "action", "send"),
        ],
        edges=[_edge("t", "wb1"), _edge("wb1", "s1"), _edge("s1", "wb2"), _edge("wb2", "s2")],
    )

    class FakeAutoRepo:
        def __init__(self, session): ...
        async def list_enabled_by_trigger(self, *, merchant_id, trigger_type):
            return [flow]

    class FakeConfig:
        async def resolve(self, key, *, merchant_id):
            return [24]  # config fallback — must be ignored when the graph provides hours

    monkeypatch.setattr(mod, "AutomationRepository", FakeAutoRepo)

    hours = await mod._resolve_reminder_lead_hours(
        object(), merchant_id=uuid.uuid4(), config=FakeConfig(), fallback=[24]
    )
    assert hours == [48, 2]


async def test_booking_reminder_hours_fallback_when_flow_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No enabled flow → the reminder anticipi come from the ConfigKey (compat)."""
    from ai_core.actions import booking as mod

    class FakeAutoRepo:
        def __init__(self, session): ...
        async def list_enabled_by_trigger(self, *, merchant_id, trigger_type):
            return []

    class FakeConfig:
        async def resolve(self, key, *, merchant_id):
            return [72, 24]

    monkeypatch.setattr(mod, "AutomationRepository", FakeAutoRepo)

    hours = await mod._resolve_reminder_lead_hours(
        object(), merchant_id=uuid.uuid4(), config=FakeConfig(), fallback=[24]
    )
    assert hours == [72, 24]


# ---- router wiring: real slot data goes to the model, not a fixed template ----


class _FakeReplyClient:
    def __init__(self, *, reply: str = "Ti va bene venerdì alle 10:45?") -> None:
        self.reply = reply
        self.chiamate: list[Any] = []

    async def complete(self, *, messages, max_tokens=None, **kw):
        from types import SimpleNamespace

        self.chiamate.append(messages)
        return SimpleNamespace(
            content=self.reply, model="gpt-5-nano", tokens_in=1, tokens_out=1, latency_ms=1, raw={}
        )


class _FakeReplyRouter:
    def __init__(self, client: _FakeReplyClient) -> None:
        self.client = client

    async def select(self, req):
        return self.client


async def test_book_slot_taken_uses_composed_reply_when_router_is_wired(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext, ghl_bundle: ResolvedGHLIntegration
) -> None:
    """With a router wired, the "slot taken" message is the model's own natural
    text (built from the real alternatives), not the fixed `format_booking_confirmation`
    template — this is the blocco-2 fix: no more hardcoded "Quello slot non è più
    disponibile" bubble when the composer is available."""
    _patch_session(monkeypatch, ghl=ghl_bundle)
    _patch_ghl_client(monkeypatch, booking_ok=False)
    sender = FakeSender()
    client = _FakeReplyClient(reply="Quel momento è appena andato, ti va bene alle 9 o alle 11?")
    router = _FakeReplyRouter(client)

    handler = BookSlotHandler(
        kek_base64="unused",
        ghl_client_id="x",
        ghl_client_secret="y",
        reply_sender=sender,
        router=router,
    )
    await handler(
        OrchestratorAction(
            kind="book_slot",
            payload={"preferred_start_iso": "2026-04-25T10:00:00+02:00"},
        ),
        turn_ctx,
    )

    assert len(sender.calls) == 1
    assert sender.calls[0]["text"] == "Quel momento è appena andato, ti va bene alle 9 o alle 11?"
    # The fixed template's telltale markers must NOT appear — this is a composed
    # sentence, not the bulleted form letter.
    assert "•" not in sender.calls[0]["text"]
    assert client.chiamate  # the composer was actually invoked


async def test_book_slot_falls_back_to_template_when_composer_fails(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext, ghl_bundle: ResolvedGHLIntegration
) -> None:
    """Fail-open: if the compose call errors, the deterministic template still
    goes out — a booking outcome is never left unsent."""
    _patch_session(monkeypatch, ghl=ghl_bundle)
    _patch_ghl_client(monkeypatch, booking_ok=False)
    sender = FakeSender()

    class _BrokenRouter:
        async def select(self, req):
            raise RuntimeError("router unavailable")

    handler = BookSlotHandler(
        kek_base64="unused",
        ghl_client_id="x",
        ghl_client_secret="y",
        reply_sender=sender,
        router=_BrokenRouter(),
    )
    await handler(
        OrchestratorAction(
            kind="book_slot",
            payload={"preferred_start_iso": "2026-04-25T10:00:00+02:00"},
        ),
        turn_ctx,
    )

    assert len(sender.calls) == 1
    assert "non è più disponibile" in sender.calls[0]["text"]


async def test_propose_slots_uses_composed_reply_when_router_is_wired(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext, ghl_bundle: ResolvedGHLIntegration
) -> None:
    """The proactive "here's what's free" offer is also composed, not the fixed
    `format_slot_proposal` bulleted template — this is what used to show up in
    the merchant inbox mislabeled "Automazione"."""
    _patch_session(monkeypatch, ghl=ghl_bundle)
    from ai_core.actions import booking as mod

    client_ghl = AsyncMock()
    client_ghl.get_free_slots = AsyncMock(
        return_value=[
            {"startTime": "2026-04-25T09:00:00+02:00"},
            {"startTime": "2026-04-25T10:00:00+02:00"},
            {"startTime": "2026-04-25T11:00:00+02:00"},
        ]
    )
    client_ghl.close = AsyncMock()
    monkeypatch.setattr(mod, "GHLClient", MagicMock(side_effect=lambda **kw: client_ghl))

    sender = FakeSender()
    client = _FakeReplyClient(reply="Le andrebbe bene sabato mattina, verso le 9 o le 10?")
    router = _FakeReplyRouter(client)
    handler = ProposeSlotsHandler(
        kek_base64="unused",
        ghl_client_id="x",
        ghl_client_secret="y",
        reply_sender=sender,
        router=router,
    )
    await handler(OrchestratorAction(kind="propose_slots", payload={}), turn_ctx)

    assert len(sender.calls) == 1
    assert sender.calls[0]["text"] == "Le andrebbe bene sabato mattina, verso le 9 o le 10?"
    assert "•" not in sender.calls[0]["text"]


# ---- _verified_free_slots: alternatives are cross-checked, not just trusted ----


def _tz():
    from zoneinfo import ZoneInfo

    return ZoneInfo("Europe/Rome")


async def test_verified_free_slots_drops_a_candidate_that_overlaps_a_booked_event() -> None:
    """The core fix: `get_free_slots` said these were free, but the calendar's
    actual booked events (`list_appointments`) say otherwise for one of them —
    it must not be offered to the customer as a "verified" alternative."""
    client = AsyncMock()
    client.list_appointments = AsyncMock(
        return_value=[
            {
                "id": "evt-1",
                "start_iso": "2026-04-25T09:00:00+02:00",
                "end_iso": "2026-04-25T09:30:00+02:00",
                "status": "confirmed",
            }
        ]
    )

    result = await _verified_free_slots(
        client,
        calendar_id="CAL-1",
        candidates=[
            "2026-04-25T09:00:00+02:00",  # overlaps the booked event
            "2026-04-25T11:00:00+02:00",  # clear
        ],
        window_start_iso="2026-04-25T00:00:00+02:00",
        window_end_iso="2026-04-26T00:00:00+02:00",
        duration_min=30,
        tz=_tz(),
    )

    assert result == ["2026-04-25T11:00:00+02:00"]


async def test_verified_free_slots_ignores_cancelled_events() -> None:
    """A cancelled appointment does not block the slot it used to occupy."""
    client = AsyncMock()
    client.list_appointments = AsyncMock(
        return_value=[
            {
                "id": "evt-1",
                "start_iso": "2026-04-25T09:00:00+02:00",
                "end_iso": "2026-04-25T09:30:00+02:00",
                "status": "cancelled",
            }
        ]
    )

    result = await _verified_free_slots(
        client,
        calendar_id="CAL-1",
        candidates=["2026-04-25T09:00:00+02:00"],
        window_start_iso="2026-04-25T00:00:00+02:00",
        window_end_iso="2026-04-26T00:00:00+02:00",
        duration_min=30,
        tz=_tz(),
    )

    assert result == ["2026-04-25T09:00:00+02:00"]


async def test_verified_free_slots_degrades_to_unfiltered_when_the_read_fails() -> None:
    """A hiccup on the verification read must not block every alternative —
    fall back to the (unverified) candidates rather than offering nothing."""
    client = AsyncMock()
    client.list_appointments = AsyncMock(side_effect=IntegrationError("ghl down", status=503))

    result = await _verified_free_slots(
        client,
        calendar_id="CAL-1",
        candidates=["2026-04-25T09:00:00+02:00", "2026-04-25T11:00:00+02:00"],
        window_start_iso="2026-04-25T00:00:00+02:00",
        window_end_iso="2026-04-26T00:00:00+02:00",
        duration_min=30,
        tz=_tz(),
    )

    assert result == ["2026-04-25T09:00:00+02:00", "2026-04-25T11:00:00+02:00"]


async def test_book_slot_taken_alternatives_are_cross_checked_end_to_end(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext, ghl_bundle: ResolvedGHLIntegration
) -> None:
    """Wiring test: a candidate that `get_free_slots` calls free but that overlaps
    a real booked event never reaches the customer, end-to-end through the
    handler (not just at the `_verified_free_slots` unit level)."""
    appt_calls = _patch_session(monkeypatch, ghl=ghl_bundle)
    ghl_client = _patch_ghl_client(monkeypatch, booking_ok=False)
    # 09:00 is one of the fixture's 3 raw slots (see `_patch_ghl_client`) and is
    # NOT the rejected 10:00 slot, so it would otherwise survive the dedup fix —
    # but a real booked event covers it.
    ghl_client.list_appointments = AsyncMock(
        return_value=[
            {
                "id": "evt-1",
                "start_iso": "2026-04-25T09:00:00+02:00",
                "end_iso": "2026-04-25T09:30:00+02:00",
                "status": "confirmed",
            }
        ]
    )
    sender = FakeSender()

    handler = BookSlotHandler(
        kek_base64="unused", ghl_client_id="x", ghl_client_secret="y", reply_sender=sender
    )
    await handler(
        OrchestratorAction(
            kind="book_slot",
            payload={"preferred_start_iso": "2026-04-25T10:00:00+02:00"},
        ),
        turn_ctx,
    )

    text = sender.calls[0]["text"]
    assert "09:00" not in text, "a slot covered by a real booked event must not be offered"
    assert "11:00" in text
    assert appt_calls == []
