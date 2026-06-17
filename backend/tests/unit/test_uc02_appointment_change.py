"""UC-02 — reschedule_slot / cancel_slot action handler unit tests.

Stubs the DB session, AppointmentRepository, and the appointment_ops service so
we exercise only the handler logic: lead-appointment resolution, the
ambiguity/no-appointment branches, and the WhatsApp confirmation text.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from ai_core.actions.appointment_change import CancelSlotHandler, RescheduleSlotHandler
from ai_core.actions.appointment_ops import AppointmentOpResult
from ai_core.conversation_service import TurnContext
from ai_core.orchestrator import OrchestratorAction


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


def _appt(start: datetime, *, minutes: int = 30) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        ghl_appointment_id="BK-1",
        start_at=start,
        end_at=start + timedelta(minutes=minutes),
        tz_name="Europe/Rome",
        status="booked",
    )


def _patch(monkeypatch, *, upcoming: list[Any], op_result: AppointmentOpResult | None = None):
    """Patch the handler's DB + service touchpoints. Returns the list that
    records calls into the appointment_ops service."""
    if op_result is None:
        op_result = AppointmentOpResult(True)
    from ai_core.actions import appointment_change as mod

    @asynccontextmanager
    async def fake_session(ctx):
        yield object()

    class FakeAppointmentRepo:
        def __init__(self, session): ...
        async def list_upcoming_for_lead(self, *, merchant_id, lead_id, now):
            return upcoming

    op_calls: list[dict] = []

    async def fake_reschedule(session, appt, **kw):
        op_calls.append({"op": "reschedule", "appt": appt, **kw})
        return op_result

    async def fake_cancel(session, appt, **kw):
        op_calls.append({"op": "cancel", "appt": appt, **kw})
        return op_result

    monkeypatch.setattr(mod, "tenant_session", fake_session)
    monkeypatch.setattr(mod, "AppointmentRepository", FakeAppointmentRepo)
    monkeypatch.setattr(mod, "reschedule_appointment", fake_reschedule)
    monkeypatch.setattr(mod, "cancel_appointment", fake_cancel)
    return op_calls


def _cancel_handler(sender: FakeSender) -> CancelSlotHandler:
    return CancelSlotHandler(
        kek_base64="unused", ghl_client_id="x", ghl_client_secret="y", reply_sender=sender
    )


def _resched_handler(sender: FakeSender) -> RescheduleSlotHandler:
    return RescheduleSlotHandler(
        kek_base64="unused", ghl_client_id="x", ghl_client_secret="y", reply_sender=sender
    )


# ---- cancel --------------------------------------------------------------


async def test_cancel_no_upcoming(monkeypatch, turn_ctx) -> None:
    op_calls = _patch(monkeypatch, upcoming=[])
    sender = FakeSender()
    await _cancel_handler(sender)(OrchestratorAction(kind="cancel_slot", payload={}), turn_ctx)
    assert op_calls == []
    assert "Non trovo appuntamenti" in sender.calls[0]["text"]


async def test_cancel_single_calls_service(monkeypatch, turn_ctx) -> None:
    appt = _appt(datetime(2026, 8, 1, 10, 0, tzinfo=UTC))
    op_calls = _patch(monkeypatch, upcoming=[appt])
    sender = FakeSender()
    await _cancel_handler(sender)(OrchestratorAction(kind="cancel_slot", payload={}), turn_ctx)
    assert len(op_calls) == 1 and op_calls[0]["op"] == "cancel"
    assert "annullato" in sender.calls[0]["text"]


async def test_cancel_ambiguous_asks_not_acts(monkeypatch, turn_ctx) -> None:
    appts = [
        _appt(datetime(2026, 8, 1, 10, 0, tzinfo=UTC)),
        _appt(datetime(2026, 8, 3, 12, 0, tzinfo=UTC)),
    ]
    op_calls = _patch(monkeypatch, upcoming=appts)
    sender = FakeSender()
    await _cancel_handler(sender)(OrchestratorAction(kind="cancel_slot", payload={}), turn_ctx)
    assert op_calls == []  # destructive op never guessed when ambiguous
    assert "più appuntamenti" in sender.calls[0]["text"]
    assert sender.calls[0]["text"].count("•") == 2


# ---- reschedule ----------------------------------------------------------


async def test_reschedule_single_with_time(monkeypatch, turn_ctx) -> None:
    appt = _appt(datetime(2026, 8, 1, 10, 0, tzinfo=UTC), minutes=45)
    op_calls = _patch(monkeypatch, upcoming=[appt])
    sender = FakeSender()
    await _resched_handler(sender)(
        OrchestratorAction(
            kind="reschedule_slot", payload={"preferred_start_iso": "2026-08-02T16:00:00"}
        ),
        turn_ctx,
    )
    assert len(op_calls) == 1 and op_calls[0]["op"] == "reschedule"
    # Naive new time interpreted in the appointment tz; prior 45-min duration kept.
    assert op_calls[0]["new_start"].isoformat() == "2026-08-02T16:00:00+02:00"
    assert op_calls[0]["new_end"].isoformat() == "2026-08-02T16:45:00+02:00"
    assert "spostato" in sender.calls[0]["text"]


async def test_reschedule_missing_time_asks(monkeypatch, turn_ctx) -> None:
    appt = _appt(datetime(2026, 8, 1, 10, 0, tzinfo=UTC))
    op_calls = _patch(monkeypatch, upcoming=[appt])
    sender = FakeSender()
    await _resched_handler(sender)(OrchestratorAction(kind="reschedule_slot", payload={}), turn_ctx)
    assert op_calls == []
    assert "Per quando" in sender.calls[0]["text"]


async def test_reschedule_ambiguous_asks(monkeypatch, turn_ctx) -> None:
    appts = [
        _appt(datetime(2026, 8, 1, 10, 0, tzinfo=UTC)),
        _appt(datetime(2026, 8, 3, 12, 0, tzinfo=UTC)),
    ]
    op_calls = _patch(monkeypatch, upcoming=appts)
    sender = FakeSender()
    await _resched_handler(sender)(
        OrchestratorAction(
            kind="reschedule_slot", payload={"preferred_start_iso": "2026-08-05T09:00:00"}
        ),
        turn_ctx,
    )
    assert op_calls == []
    assert "più appuntamenti" in sender.calls[0]["text"]
