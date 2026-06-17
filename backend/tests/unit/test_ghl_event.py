"""G-GHL-EVENT + B3 — routing of GHL data webhooks.

`handle_ghl_event` was a no-op log. These tests pin the new routing:
  * ContactUpdate  → lead name/email synced (UC-01 identity → UC-05 scoring).
  * OpportunityStatusUpdate → lead.pipeline_stage_id mirrored (UC-04).
  * a failed-call result → WhatsApp takeover primed (UC-03, blocker B3).
  * a completed call → not actionable.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
import workers.conversation.handlers as mod

pytestmark = pytest.mark.asyncio


@dataclass
class FakeLead:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    ghl_contact_id: str | None = None
    name: str | None = None
    email: str | None = None
    pipeline_stage_id: str | None = None
    meta: dict | None = None


@dataclass
class FakeConv:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    meta: dict | None = None


class _FakeWA:
    phone_number_id = "PN1"


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    lead: FakeLead | None,
    active_conv: FakeConv | None = None,
    capture: dict[str, Any],
) -> None:
    @asynccontextmanager
    async def fake_session():
        yield object()

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def get_by_ghl_contact_id(self, *, merchant_id, ghl_contact_id):
            return lead

        async def get_by_phone(self, *, merchant_id, phone):
            return lead

        async def update_contact_fields(self, lead_id, *, name=None, email=None):
            capture["contact_fields"] = {"name": name, "email": email}

        async def set_pipeline_stage(self, lead_id, *, stage_id):
            capture["stage_id"] = stage_id

    class FakeConvRepo:
        def __init__(self, session): ...
        async def get_active(self, *, merchant_id, wa_contact_phone):
            return active_conv

        async def create(self, *, merchant_id, lead_id, wa_phone_number_id, wa_contact_phone):
            conv = FakeConv()
            capture["created_conv"] = conv
            return conv

        async def touch_last_message(self, conversation_id):
            capture["touched"] = conversation_id

    class FakeIntegrationRepo:
        def __init__(self, session, *, kek_base64): ...
        async def resolve_whatsapp_by_merchant(self, merchant_id):
            return _FakeWA()

    class FakeSettings:
        integrations_kek_base64 = ""

    monkeypatch.setattr(mod, "session_scope", fake_session)
    monkeypatch.setattr(mod, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(mod, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(mod, "IntegrationRepository", FakeIntegrationRepo)
    monkeypatch.setattr(mod, "get_settings", lambda: FakeSettings())


async def test_contact_update_syncs_name_and_email(monkeypatch: pytest.MonkeyPatch) -> None:
    lead = FakeLead(ghl_contact_id="C1", meta={})
    capture: dict[str, Any] = {}
    _patch(monkeypatch, lead=lead, capture=capture)

    res = await handle_ghl_event_call(
        "ContactUpdate", {"id": "C1", "firstName": "Mario", "lastName": "Rossi", "email": "m@x.it"}
    )

    assert res["matched"] is True
    assert capture["contact_fields"] == {"name": "Mario Rossi", "email": "m@x.it"}


async def test_opportunity_update_mirrors_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    lead = FakeLead(ghl_contact_id="C1", meta={})
    capture: dict[str, Any] = {}
    _patch(monkeypatch, lead=lead, capture=capture)

    res = await handle_ghl_event_call(
        "OpportunityStatusUpdate",
        {"contactId": "C1", "pipelineStageId": "STAGE-2", "id": "OPP-9"},
    )

    assert res["matched"] is True
    assert capture["stage_id"] == "STAGE-2"
    assert lead.meta["ghl_opportunity_id"] == "OPP-9"


async def test_failed_call_primes_whatsapp_takeover(monkeypatch: pytest.MonkeyPatch) -> None:
    lead = FakeLead(ghl_contact_id="C1")
    capture: dict[str, Any] = {}
    _patch(monkeypatch, lead=lead, active_conv=None, capture=capture)

    res = await handle_ghl_event_call(
        "OutboundCall", {"callStatus": "no answer", "contactId": "C1", "phone": "39333000000"}
    )

    assert res["handled"] is True
    assert res["outcome"] == "no_answer"
    conv = capture["created_conv"]
    assert conv.meta["origin"] == "call_failed"
    assert conv.meta["call_outcome"] == "no_answer"
    assert capture["touched"] == conv.id


async def test_completed_call_is_not_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    capture: dict[str, Any] = {}
    _patch(monkeypatch, lead=None, capture=capture)

    res = await handle_ghl_event_call(
        "InboundCall", {"callStatus": "completed", "phone": "39333000000"}
    )

    assert res["handled"] is False
    assert res["reason"] == "outcome_not_actionable"
    assert "created_conv" not in capture


async def handle_ghl_event_call(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await mod.handle_ghl_event({}, str(uuid.uuid4()), event_type, payload)
