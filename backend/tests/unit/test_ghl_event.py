"""G-GHL-EVENT + B3 + ADR 0016 — routing of GHL data webhooks.

`handle_ghl_event` was a no-op log. These tests pin the routing:
  * ContactUpdate  → lead name/email synced (UC-01 identity → UC-05 scoring).
  * OpportunityStatusUpdate → lead.pipeline_stage_id mirrored (UC-04).
  * a failed-call result → WhatsApp takeover primed (UC-03, blocker B3).
  * a completed call → not actionable.
  * ContactCreate/OpportunityCreate (ADR 0016) → lead get-or-create with phone
    normalisation, conversation provisioning, and the `lead.crm_created` /
    `opportunity.created` trigger emissions (echo- and opt-out-guarded).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    phone: str | None = None
    pipeline_stage_id: str | None = None
    meta: dict | None = None
    opted_out_at: datetime | None = None


@dataclass
class FakeConv:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    lead_id: uuid.UUID | None = None
    meta: dict | None = None


class _FakeWA:
    phone_number_id = "PN1"


class _FakeRedis:
    def __init__(self, capture: dict[str, Any]) -> None:
        self._capture = capture

    async def enqueue_job(self, name: str, *args: Any, **kwargs: Any) -> None:
        self._capture.setdefault("enqueued", []).append(
            {"name": name, "args": args, "kwargs": kwargs}
        )


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    lead: FakeLead | None,
    active_conv: FakeConv | None = None,
    wa: Any = None,
    capture: dict[str, Any],
) -> None:
    if wa is None:
        wa = _FakeWA()

    @asynccontextmanager
    async def fake_session():
        yield object()

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def get_by_ghl_contact_id(self, *, merchant_id, ghl_contact_id):
            return lead

        async def get_by_phone(self, *, merchant_id, phone):
            capture["looked_up_phone"] = phone
            return lead

        async def upsert_by_phone_flagged(self, *, merchant_id, phone, campaign=None):
            created = FakeLead(phone=phone)
            capture["upserted"] = {"phone": phone, "campaign": campaign}
            capture["upserted_lead"] = created
            return created, True

        async def update_contact_fields(self, lead_id, *, name=None, email=None):
            capture["contact_fields"] = {"name": name, "email": email}

        async def set_pipeline_stage(self, lead_id, *, stage_id):
            capture["stage_id"] = stage_id

    class FakeConvRepo:
        def __init__(self, session): ...
        async def get_active(self, *, merchant_id, wa_contact_phone):
            return active_conv

        async def create(self, *, merchant_id, lead_id, wa_phone_number_id, wa_contact_phone):
            conv = FakeConv(lead_id=lead_id)
            capture["created_conv"] = conv
            capture["created_conv_phone"] = wa_contact_phone
            return conv

        async def touch_last_message(self, conversation_id):
            capture["touched"] = conversation_id

    class FakeIntegrationRepo:
        def __init__(self, session, *, kek_base64): ...
        async def resolve_whatsapp_by_merchant(self, merchant_id):
            return wa

    class FakeMarketplaceRepo:
        def __init__(self, session, *, kek_base64): ...
        async def merchant_id_for_location(self, location_id):
            # Marketplace events carry locationId; resolve to a merchant.
            return uuid.uuid4()

    class FakeAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kwargs):
            capture.setdefault("emitted", []).append(kwargs)

    async def fake_tenant_id(session, merchant_id):
        return uuid.uuid4()

    class FakeSettings:
        integrations_kek_base64 = ""

    monkeypatch.setattr(mod, "session_scope", fake_session)
    monkeypatch.setattr(mod, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(mod, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(mod, "IntegrationRepository", FakeIntegrationRepo)
    monkeypatch.setattr(mod, "GHLMarketplaceRepository", FakeMarketplaceRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", FakeAnalyticsRepo)
    monkeypatch.setattr(mod, "_tenant_id_for_merchant", fake_tenant_id)
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


# --- ADR 0016: ContactCreate / OpportunityCreate ------------------------------


async def test_contact_create_new_lead_creates_and_emits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture: dict[str, Any] = {}
    _patch(monkeypatch, lead=None, capture=capture)

    res = await handle_ghl_event_call(
        "ContactCreate",
        {"id": "C9", "firstName": "Anna", "email": "a@x.it", "phone": "+39 333 000-0000"},
    )

    assert res["matched"] is True
    assert res["created"] is True
    assert res["emitted"] == ["lead.crm_created"]
    # GHL's E.164 phone is normalised to the WhatsApp digits identity.
    assert capture["upserted"] == {"phone": "393330000000", "campaign": "ghl_crm"}
    new_lead = capture["upserted_lead"]
    assert new_lead.ghl_contact_id == "C9"
    # A conversation is provisioned so the automation engine has a send context.
    assert capture["created_conv"].lead_id == new_lead.id
    assert capture["created_conv_phone"] == "393330000000"
    (event,) = capture["emitted"]
    assert event["event_type"] == "lead.crm_created"
    assert event["subject_id"] == new_lead.id


async def test_opportunity_create_new_lead_emits_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture: dict[str, Any] = {}
    _patch(monkeypatch, lead=None, capture=capture)

    res = await handle_ghl_event_call(
        "OpportunityCreate",
        {
            "id": "OPP-1",
            "contactId": "C1",
            "pipelineId": "P1",
            "pipelineStageId": "S1",
            "phone": "+393330000000",
        },
    )

    assert res["matched"] is True
    assert res["emitted"] == ["lead.crm_created", "opportunity.created"]
    assert capture["stage_id"] == "S1"
    new_lead = capture["upserted_lead"]
    assert new_lead.meta["ghl_opportunity_id"] == "OPP-1"
    opp_event = capture["emitted"][1]
    assert opp_event["event_type"] == "opportunity.created"
    assert opp_event["properties"]["pipeline_id"] == "P1"
    assert opp_event["properties"]["stage_id"] == "S1"
    assert opp_event["properties"]["ghl_opportunity_id"] == "OPP-1"


async def test_opportunity_create_echo_of_bot_writeback_does_not_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # move_pipeline/booking stash the opportunity id they created on the lead;
    # the webhook echo of that write-back must not fire a cold-outreach flow.
    lead = FakeLead(ghl_contact_id="C1", phone="393330000000", meta={"ghl_opportunity_id": "OPP-9"})
    capture: dict[str, Any] = {}
    _patch(monkeypatch, lead=lead, capture=capture)

    res = await handle_ghl_event_call(
        "OpportunityCreate",
        {"id": "OPP-9", "contactId": "C1", "pipelineId": "P1", "pipelineStageId": "S1"},
    )

    assert res["matched"] is True
    assert res["created"] is False
    assert res["emitted"] == []
    assert "upserted" not in capture


async def test_contact_create_existing_lead_syncs_without_emitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead = FakeLead(ghl_contact_id="C1", phone="393330000000", meta={})
    capture: dict[str, Any] = {}
    _patch(monkeypatch, lead=lead, capture=capture)

    res = await handle_ghl_event_call(
        "ContactCreate", {"id": "C1", "firstName": "Anna", "phone": "+393330000000"}
    )

    assert res["matched"] is True
    assert res["created"] is False
    assert res["emitted"] == []
    assert capture["contact_fields"] == {"name": "Anna", "email": None}


async def test_opportunity_create_without_phone_requeues_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture: dict[str, Any] = {}
    _patch(monkeypatch, lead=None, capture=capture)
    ctx = {"redis": _FakeRedis(capture)}

    payload = {"id": "OPP-1", "contactId": "C-unknown", "pipelineId": "P1"}
    res = await mod.handle_ghl_event(ctx, str(uuid.uuid4()), "OpportunityCreate", payload)
    assert res["reason"] == "requeued_no_phone"
    (job,) = capture["enqueued"]
    assert job["name"] == "handle_ghl_event"
    assert job["args"][2]["_reloop_requeued"] is True

    # The requeued copy misses again → dropped, no second requeue.
    res2 = await mod.handle_ghl_event(
        ctx, str(uuid.uuid4()), "OpportunityCreate", {**payload, "_reloop_requeued": True}
    )
    assert res2["reason"] == "no_phone"
    assert len(capture["enqueued"]) == 1


async def test_crm_create_skips_emit_for_opted_out_lead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead = FakeLead(
        ghl_contact_id="C1",
        phone="393330000000",
        meta={},
        opted_out_at=datetime.now(tz=UTC),
    )
    capture: dict[str, Any] = {}
    _patch(monkeypatch, lead=lead, capture=capture)

    res = await handle_ghl_event_call(
        "OpportunityCreate",
        {"id": "OPP-2", "contactId": "C1", "pipelineId": "P1", "pipelineStageId": "S1"},
    )

    assert res["matched"] is True
    assert res["emitted"] == []


async def handle_ghl_event_call(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await mod.handle_ghl_event({}, str(uuid.uuid4()), event_type, payload)
