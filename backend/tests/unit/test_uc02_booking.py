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

from ai_core.actions.booking import BookSlotHandler
from ai_core.conversation_service import TurnContext
from ai_core.orchestrator import OrchestratorAction
from db import ResolvedGHLIntegration
from shared import IntegrationError


@dataclass
class FakeSender:
    calls: list[dict] = field(default_factory=list)

    async def send(self, *, access_token, phone_number_id, to_phone, text, provider="meta"):
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
        whatsapp_access_token="wa-token",
        phone_number_id="PNID-1",
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


def _patch_session(monkeypatch, *, ghl: ResolvedGHLIntegration | None):
    """Replace the action-handler's DB touchpoints with fakes."""
    from ai_core.actions import booking as mod

    @asynccontextmanager
    async def fake_session(ctx):
        yield object()

    class FakeIntegrationRepo:
        def __init__(self, session, *, kek_base64): ...
        async def resolve_ghl(self, merchant_id):
            return ghl

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def update_score(self, lead_id, *, score, reasons): ...

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
    monkeypatch.setattr(mod, "ConfigResolver", FakeConfig)


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


async def test_book_slot_taken_proposes_alternatives(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext, ghl_bundle: ResolvedGHLIntegration
) -> None:
    _patch_session(monkeypatch, ghl=ghl_bundle)
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


async def test_book_slot_no_ghl_integration_sends_graceful_fallback(
    monkeypatch: pytest.MonkeyPatch, turn_ctx: TurnContext
) -> None:
    _patch_session(monkeypatch, ghl=None)
    sender = FakeSender()

    handler = BookSlotHandler(
        kek_base64="unused",
        ghl_client_id="x",
        ghl_client_secret="y",
        reply_sender=sender,
    )

    await handler(OrchestratorAction(kind="book_slot", payload={}), turn_ctx)

    assert len(sender.calls) == 1
    assert "non riesco" in sender.calls[0]["text"]
