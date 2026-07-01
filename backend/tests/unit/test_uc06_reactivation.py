"""UC-06 — reactivation opt-out + send decisioning.

Covers the Sprint-5 additions:
  * `_is_opt_out` — STOP/CANCELLA detection (exact, normalised) that drives the
    opt-out intercept in `handle_inbound_persist`.
  * reactivation `_maybe_send`: sends via an approved template (dormant leads are
    outside the 24h window), and skips cleanly when no template is configured.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from workers import outbound
from workers.scheduler import reactivation

from ai_core.conversation_service import _is_opt_out
from db import ReactivationCandidate, ResolvedWhatsAppIntegration


def test_is_opt_out_matches_exact_keywords() -> None:
    for msg in ["STOP", "stop", " Stop ", "CANCELLA", "annulla", "unsubscribe", "Stop."]:
        assert _is_opt_out(msg) is True


def test_is_opt_out_ignores_sentences() -> None:
    for msg in ["stop un attimo", "non cancellare", "vorrei fermare l'ordine", "ok grazie"]:
        assert _is_opt_out(msg) is False


# ---- reactivation send ----------------------------------------------------


@dataclass
class FakeRedis:
    _store: dict | None = None

    async def set(self, key, value, *, nx=False, ex=None):
        if self._store is None:
            self._store = {}
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True


@dataclass
class FakeStep:
    window_policy: str = "auto"
    free_text: str | None = None
    template_name: str | None = None
    template_language: str | None = "it"
    template_variables: list | None = None
    variable_mapping: dict | None = None
    template_approved: bool = False
    template_header_image_url: str | None = None
    flow_enabled: bool = True
    step_enabled: bool = True


def _candidate() -> ReactivationCandidate:
    now = datetime.now(tz=UTC)
    return ReactivationCandidate(
        lead_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        phone="39333000000",
        wa_phone_number_id="PNID-1",
        last_interaction_at=now - timedelta(days=120),
        attempts_sent=0,
        last_reactivation_at=None,
        name="Mario",
    )


class _FakeConv:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


def _patch(
    monkeypatch,
    *,
    step: FakeStep | None,
    records: list,
    sends: list,
    persisted: list | None = None,
) -> None:
    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield object()

    class FakeConvRepo:
        def __init__(self, session): ...
        async def get_active(self, *, merchant_id, wa_contact_phone):
            return None

        async def create(self, **kw):
            return _FakeConv()

    class FakeMessageRepo:
        def __init__(self, session): ...
        async def persist_outbound_message(self, **kw):
            if persisted is not None:
                persisted.append(kw)
            return object()

    class FakeConfig:
        def __init__(self, session): ...
        async def resolve(self, key, *, merchant_id):
            return {
                "reactivation.dormant_days": 90,
                "reactivation.interval_days": 7,
                "reactivation.max_attempts": 3,
            }.get(getattr(key, "value", str(key)))

    async def fake_resolve_lifecycle_step(
        session, *, merchant_id, system_key, attempt_index, context
    ):
        return step

    async def fake_resolve_lifecycle_plan(session, *, merchant_id, system_key, context):
        # No enabled system flow → scheduler sources timing from ConfigKeys.
        return None

    class FakeIntegrationRepo:
        def __init__(self, session, *, kek_base64): ...
        async def resolve_whatsapp(self, phone_number_id):
            return ResolvedWhatsAppIntegration(
                merchant_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                phone_number_id=phone_number_id,
                api_key="k",
                waba_base_url=None,
                meta={},
            )

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def record_reactivation_sent(self, lead_id):
            records.append(lead_id)

    class FakeAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kw): ...

    class FakeWAClient:
        async def send_text(self, *, to_phone, text):
            sends.append(("text", text))
            return {"messages": [{"id": "wamid.t"}]}

        async def send_template(self, *, to_phone, template_name, language, components):
            sends.append(("template", template_name))
            return {"messages": [{"id": "wamid.tmpl"}]}

        async def close(self): ...

    monkeypatch.setattr(reactivation, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(reactivation, "ConfigResolver", FakeConfig)
    monkeypatch.setattr(reactivation, "resolve_lifecycle_step", fake_resolve_lifecycle_step)
    monkeypatch.setattr(reactivation, "resolve_lifecycle_plan", fake_resolve_lifecycle_plan)
    monkeypatch.setattr(reactivation, "IntegrationRepository", FakeIntegrationRepo)
    monkeypatch.setattr(reactivation, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(reactivation, "AnalyticsRepository", FakeAnalyticsRepo)
    monkeypatch.setattr(reactivation, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(outbound, "MessageRepository", FakeMessageRepo)
    monkeypatch.setattr(reactivation, "build_whatsapp_sender", lambda **kw: FakeWAClient())


async def test_reactivation_sends_template_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = FakeStep(
        template_name="reactivation_it",
        template_approved=True,
        template_variables=[],
        variable_mapping={},
    )
    records: list = []
    sends: list = []
    persisted: list = []
    _patch(monkeypatch, step=step, records=records, sends=sends, persisted=persisted)

    sent = await reactivation._maybe_send(
        _candidate(), now=datetime.now(tz=UTC), redis=FakeRedis(), kek="unused"
    )

    assert sent is True
    assert sends == [("template", "reactivation_it")]
    assert len(records) == 1
    # #29: the reactivation is persisted as an outbound Message with its wa id.
    assert len(persisted) == 1
    assert persisted[0]["wa_message_id"] == "wamid.tmpl"
    assert persisted[0]["status"] == "sent"


async def test_reactivation_skips_outside_window_without_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No flow step → dormant lead is outside the 24h window with no approved
    # template → decide_outbound returns SKIP, nothing sent.
    records: list = []
    sends: list = []
    _patch(monkeypatch, step=None, records=records, sends=sends)

    sent = await reactivation._maybe_send(
        _candidate(), now=datetime.now(tz=UTC), redis=FakeRedis(), kek="unused"
    )

    assert sent is False
    assert sends == []
    assert records == []
