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

from ai_core.actions.booking import BookSlotHandler, ProposeSlotsHandler
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

    @asynccontextmanager
    async def fake_session(ctx):
        yield object()

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

    monkeypatch.setattr(mod, "tenant_session", fake_session)
    monkeypatch.setattr(mod, "IntegrationRepository", FakeIntegrationRepo)
    monkeypatch.setattr(mod, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", FakeAnalyticsRepo)
    monkeypatch.setattr(mod, "AppointmentRepository", FakeAppointmentRepo)
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
    assert sender.calls[0]["text"].count("•") == 3
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
    # Internal calendar fallback → local_only confirmation, not "ricontatteremo"
    assert "registrato" in sender.calls[0]["text"].lower()
    assert "operatore" in sender.calls[0]["text"].lower()
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
    """Senza GHL: l'appuntamento viene salvato localmente e la conferma WhatsApp
    informa il cliente che sarà confermato da un operatore."""
    appt_calls = _patch_session(monkeypatch, ghl=None)
    sender = FakeSender()

    handler = BookSlotHandler(
        kek_base64="unused",
        ghl_client_id="x",
        ghl_client_secret="y",
        reply_sender=sender,
    )

    await handler(OrchestratorAction(kind="book_slot", payload={}), turn_ctx)

    # Il messaggio deve confermare la prenotazione locale, non dare un errore.
    assert len(sender.calls) == 1
    assert "operatore" in sender.calls[0]["text"]
    assert "non riesco" not in sender.calls[0]["text"]

    # Il record locale deve essere stato scritto con ghl_appointment_id=None.
    assert len(appt_calls) == 1
    assert appt_calls[0]["ghl_appointment_id"] is None
    assert appt_calls[0]["source"] == "bot_local"
