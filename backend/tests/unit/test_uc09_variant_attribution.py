"""UC-09 (blocker B2) — conversion events must carry the A/B variant_id.

`ABRepository.metrics` attributes conversions to a variant by filtering
`AnalyticsEvent.variant_id IN (<assigned variants>)`. If the action handlers
emit `booking.created` / `lead_score_changed` / `pipeline.moved` with
`variant_id=None` (the pre-fix behaviour), the filter matches nothing and every
experiment reports zero conversions for every variant — so no winner can ever
be declared. These tests pin that each handler forwards `turn_ctx.variant_id`
into `analytics.emit`.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_core.conversation_service import TurnContext
from ai_core.orchestrator import OrchestratorAction
from db import ResolvedGHLIntegration

VARIANT = "variant-b"


def _turn_ctx() -> TurnContext:
    return TurnContext(
        tenant_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        lead_phone="39333000000",
        phone_number_id="PNID-1",
        api_key="test-channel-key",
        variant_id=VARIANT,
    )


def test_turn_context_variant_id_defaults_to_none() -> None:
    ctx = TurnContext(
        tenant_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        lead_phone="39333000000",
        phone_number_id="PNID-1",
    )
    assert ctx.variant_id is None


# ---- scoring -------------------------------------------------------------


@dataclass
class _FakeLead:
    id: uuid.UUID
    score: int = 0


async def test_score_event_carries_variant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_core.actions import scoring as mod
    from ai_core.actions.scoring import UpdateScoreHandler

    ctx = _turn_ctx()
    events: list[dict] = []

    @asynccontextmanager
    async def fake_session(_ctx: Any):
        yield object()

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def get_by_phone(self, *, merchant_id, phone):
            return _FakeLead(id=ctx.lead_id, score=10)

        async def update_score(self, lead_id, *, score, reasons): ...
        async def merge_content_signals(self, lead_id, new_signals):
            return {k: True for k, v in new_signals.items() if v}

    class FakeAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kw):
            events.append(kw)

    class FakeConfig:
        def __init__(self, session): ...
        async def resolve(self, key, *, merchant_id):
            return {"scoring.hot_threshold": 80, "scoring.cold_threshold": 30}.get(
                getattr(key, "value", str(key))
            )

    monkeypatch.setattr(mod, "tenant_session", fake_session)
    monkeypatch.setattr(mod, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", FakeAnalyticsRepo)
    monkeypatch.setattr(mod, "ConfigResolver", FakeConfig)

    await UpdateScoreHandler()(
        OrchestratorAction(kind="update_score", payload={"signals": {"has_name": True}}),
        ctx,
    )

    assert events and events[0]["event_type"] == "lead_score_changed"
    assert events[0]["variant_id"] == VARIANT


# ---- pipeline ------------------------------------------------------------


@dataclass
class _FakePipelineLead:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    pipeline_stage_id: str | None = None
    meta: dict | None = field(default_factory=lambda: {"ghl_opportunity_id": "OPP-1"})


async def test_pipeline_event_carries_variant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_core.actions import pipeline as mod
    from ai_core.actions.pipeline import MovePipelineHandler

    ctx = _turn_ctx()
    events: list[dict] = []

    @asynccontextmanager
    async def fake_session(_ctx: Any):
        yield object()

    class FakeIntegrationRepo:
        def __init__(self, session, *, kek_base64): ...
        async def resolve_ghl(self, merchant_id):
            return ResolvedGHLIntegration(
                merchant_id=ctx.merchant_id,
                tenant_id=ctx.tenant_id,
                access_token="at",
                refresh_token="rt",
                expires_at=0,
                location_id="loc-1",
                meta={},
            )

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def get_by_phone(self, *, merchant_id, phone):
            return _FakePipelineLead()

        async def update_contact_fields(self, lead_id, *, name=None, email=None): ...

    class FakeAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kw):
            events.append(kw)

    class FakeConfig:
        def __init__(self, session): ...
        async def resolve(self, key, *, merchant_id):
            return {
                "pipeline.qualified_stage_id": "STAGE-QUALIFIED",
                "pipeline.default_pipeline_id": "PIPE-1",
            }.get(getattr(key, "value", str(key)))

    client = AsyncMock()
    client.upsert_contact = AsyncMock(return_value={"contact": {"id": "CT-1"}})
    client.move_opportunity = AsyncMock(return_value={"id": "OPP-1"})
    client.create_opportunity = AsyncMock(return_value={"id": "OPP-NEW"})
    client.close = AsyncMock()

    monkeypatch.setattr(mod, "tenant_session", fake_session)
    monkeypatch.setattr(mod, "IntegrationRepository", FakeIntegrationRepo)
    monkeypatch.setattr(mod, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", FakeAnalyticsRepo)
    monkeypatch.setattr(mod, "ConfigResolver", FakeConfig)
    monkeypatch.setattr(mod, "GHLClient", MagicMock(side_effect=lambda **kw: client))

    await MovePipelineHandler(kek_base64="unused", ghl_client_id="x", ghl_client_secret="y")(
        OrchestratorAction(kind="move_pipeline", payload={}), ctx
    )

    assert events and events[-1]["event_type"] == "pipeline.moved"
    assert events[-1]["variant_id"] == VARIANT


# ---- booking -------------------------------------------------------------


@dataclass
class _FakeSender:
    calls: list[dict] = field(default_factory=list)

    async def send(self, *, phone_number_id, api_key, to_phone, text, waba_base_url=None):
        self.calls.append({"to": to_phone, "text": text})
        return "wamid.confirm"


async def test_booking_event_carries_variant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_core.actions import booking as mod
    from ai_core.actions.booking import BookSlotHandler

    ctx = _turn_ctx()
    events: list[dict] = []

    @asynccontextmanager
    async def fake_session(_ctx: Any):
        yield object()

    class FakeIntegrationRepo:
        def __init__(self, session, *, kek_base64): ...
        async def resolve_ghl(self, merchant_id):
            return ResolvedGHLIntegration(
                merchant_id=ctx.merchant_id,
                tenant_id=ctx.tenant_id,
                access_token="at",
                refresh_token="rt",
                expires_at=0,
                location_id="loc-1",
                meta={},
            )

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def get_by_phone(self, *, merchant_id, phone):
            return None

        async def update_score(self, lead_id, *, score, reasons): ...

    class FakeAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kw):
            events.append(kw)

    class FakeConfig:
        def __init__(self, session): ...
        async def resolve(self, key, *, merchant_id):
            return {
                "booking.default_calendar_id": "CAL-1",
                "booking.default_duration_min": 30,
            }.get(getattr(key, "value", str(key)))

    class FakeAutomationRepo:
        def __init__(self, session): ...
        async def get_by_system_key(self, merchant_id, system_key):
            return None

    client = AsyncMock()
    client.upsert_contact = AsyncMock(return_value={"contact": {"id": "CT-1"}})
    client.create_booking = AsyncMock(return_value={"id": "BK-1"})
    client.close = AsyncMock()

    monkeypatch.setattr(mod, "tenant_session", fake_session)
    monkeypatch.setattr(mod, "IntegrationRepository", FakeIntegrationRepo)
    monkeypatch.setattr(mod, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", FakeAnalyticsRepo)
    monkeypatch.setattr(mod, "AutomationRepository", FakeAutomationRepo)
    monkeypatch.setattr(mod, "ConfigResolver", FakeConfig)
    monkeypatch.setattr(mod, "GHLClient", MagicMock(side_effect=lambda **kw: client))

    await BookSlotHandler(
        kek_base64="unused", ghl_client_id="x", ghl_client_secret="y", reply_sender=_FakeSender()
    )(
        OrchestratorAction(
            kind="book_slot", payload={"preferred_start_iso": "2026-07-15T15:00:00"}
        ),
        ctx,
    )

    assert events and events[-1]["event_type"] == "booking.created"
    assert events[-1]["variant_id"] == VARIANT
