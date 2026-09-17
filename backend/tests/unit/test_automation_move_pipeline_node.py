"""Automation `move_pipeline` node (unit) — ADR 0033.

Covers the deterministic canvas counterpart to the AI-dispatched `move_pipeline`
action: same handler (`MovePipelineHandler`), invoked directly with a
synthesised `TurnContext`/`OrchestratorAction` since this node must work in a
flow with no `ai_reply`/`ai_check` node (so `ai_deps` is never built).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar
from uuid import uuid4

import pytest
from workers.automation import engine
from workers.automation.engine import RunContext

from ai_core.orchestrator import OrchestratorAction


def _run_ctx(**over: Any) -> RunContext:
    base: dict[str, Any] = {
        "phone": "393331112233",
        "wa_phone_number_id": "pnid",
        "within_window": True,
        "score": 85,
        "temperature": "hot",
        "name": "Mario Rossi",
        "last_message": "sì, andiamo avanti",
        "lead_id": uuid4(),
        "conversation_id": uuid4(),
        "tenant_id": uuid4(),
        "merchant_id": uuid4(),
    }
    base.update(over)
    return RunContext(**base)


def _settings() -> Any:
    return SimpleNamespace(
        integrations_kek_base64="k", ghl_client_id="cid", ghl_client_secret="csecret"
    )


def _node(config: dict | None = None) -> Any:
    return SimpleNamespace(node_key="n", kind="action", type="move_pipeline", config=config or {})


class _FakeHandler:
    """Records the (action, turn_ctx) it's called with; never touches the network."""

    instances: ClassVar[list[_FakeHandler]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[tuple[Any, Any]] = []
        _FakeHandler.instances.append(self)

    async def __call__(self, action: Any, turn_ctx: Any) -> None:
        self.calls.append((action, turn_ctx))


class _RaisingHandler(_FakeHandler):
    async def __call__(self, action: Any, turn_ctx: Any) -> None:
        await super().__call__(action, turn_ctx)
        raise RuntimeError("ghl down")


@pytest.fixture(autouse=True)
def _reset_instances() -> None:
    _FakeHandler.instances = []


async def test_skips_without_lead(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "MovePipelineHandler", _FakeHandler)
    rc = _run_ctx(lead_id=None)

    ok = await engine._do_move_pipeline(_node(), {}, rc, settings=_settings())

    assert ok is False
    assert _FakeHandler.instances == []  # never even constructed


async def test_dispatches_with_default_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "MovePipelineHandler", _FakeHandler)
    rc = _run_ctx()

    ok = await engine._do_move_pipeline(_node(), {}, rc, settings=_settings())

    assert ok is False  # sends no WhatsApp — pure CRM side effect
    handler = _FakeHandler.instances[0]
    assert handler.kwargs == {
        "kek_base64": "k",
        "ghl_client_id": "cid",
        "ghl_client_secret": "csecret",
    }
    action, turn_ctx = handler.calls[0]
    assert isinstance(action, OrchestratorAction)
    assert action.kind == "move_pipeline"
    assert action.payload == {}  # no stage_id/reason on the node → handler's own default
    assert turn_ctx.tenant_id == rc.tenant_id
    assert turn_ctx.merchant_id == rc.merchant_id
    assert turn_ctx.lead_id == rc.lead_id
    assert turn_ctx.conversation_id == rc.conversation_id
    assert turn_ctx.lead_phone == rc.phone
    assert turn_ctx.phone_number_id == rc.wa_phone_number_id


async def test_passes_stage_override_and_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "MovePipelineHandler", _FakeHandler)
    rc = _run_ctx()
    cfg = {"stage_id": "  stage-42  ", "reason": " qualificato dal flusso "}

    await engine._do_move_pipeline(_node(cfg), cfg, rc, settings=_settings())

    action, _ = _FakeHandler.instances[0].calls[0]
    assert action.payload == {"stage_id": "stage-42", "reason": "qualificato dal flusso"}


async def test_passes_pipeline_id_alongside_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uno stage appartiene a una pipeline: scelto dalla tendina pipeline+stage,
    il pipeline_id deve arrivare all'handler insieme allo stage_id, o rischia di
    essere risolto da lead.meta/config invece che dal valore scelto sul nodo."""
    monkeypatch.setattr(engine, "MovePipelineHandler", _FakeHandler)
    rc = _run_ctx()
    cfg = {"pipeline_id": "  pipe-1  ", "stage_id": "stage-42"}

    await engine._do_move_pipeline(_node(cfg), cfg, rc, settings=_settings())

    action, _ = _FakeHandler.instances[0].calls[0]
    assert action.payload == {"pipeline_id": "pipe-1", "stage_id": "stage-42"}


async def test_handler_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "MovePipelineHandler", _RaisingHandler)
    rc = _run_ctx()

    ok = await engine._do_move_pipeline(_node(), {}, rc, settings=_settings())

    assert ok is False  # a GHL failure must not abort the rest of the walk
