"""Unit tests for the `ai_reply` automation node — engine + orchestrator.

Fakes the orchestrator / sender / dispatcher (no DB, no LLM, no network):
  - in-window  → free text sent;
  - outside window with `freeform_only` → skipped, nothing sent;
  - human takeover (`ai_paused`) → skipped, orchestrator never called;
  - AI-emitted actions are dispatched after the send;
  - `run_proactive` filters actions by `allowed_actions` and threads `force_model`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from workers.automation.engine import AiReplyDeps, RunContext, _do_ai_reply

from ai_core.orchestrator import (
    ConversationOrchestrator,
    OrchestratorAction,
    OrchestratorResponse,
)


def _run_ctx(*, within_window: bool = True, ai_paused: bool = False) -> RunContext:
    return RunContext(
        phone="393331112233",
        wa_phone_number_id="pnid",
        within_window=within_window,
        score=50,
        temperature="warm",
        name="Mario Rossi",
        last_message="",
        lead_id=uuid4(),
        conversation_id=uuid4(),
        tenant_id=uuid4(),
        merchant_id=uuid4(),
        api_key="k",
        waba_base_url=None,
        ai_paused=ai_paused,
    )


class _FakeSender:
    def __init__(self) -> None:
        self.text: str | None = None
        self.template: str | None = None

    async def send_text(self, *, to_phone: str, text: str) -> dict[str, Any]:
        self.text = text
        return {"messages": [{"id": "wamid.text"}]}

    async def send_template(
        self, *, to_phone: str, template_name: str, language: str, components: list
    ) -> dict[str, Any]:
        self.template = template_name
        return {"messages": [{"id": "wamid.tpl"}]}


class _FakeOrchestrator:
    def __init__(
        self, reply: str = "ciao", actions: list[OrchestratorAction] | None = None
    ) -> None:
        self.reply = reply
        self.actions = actions or []
        self.called = False
        self.last_kwargs: dict[str, Any] = {}

    async def run_proactive(self, ctx: Any, **kwargs: Any) -> OrchestratorResponse:
        self.called = True
        self.last_kwargs = kwargs
        return OrchestratorResponse(
            reply_text=self.reply,
            actions=self.actions,
            model="fake",
            tokens_in=1,
            tokens_out=1,
            latency_ms=1,
        )


class _FakeDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[OrchestratorAction] | None = None
        self.turn_ctx: Any = None

    async def dispatch(self, actions: list[OrchestratorAction], ctx: Any) -> None:
        self.dispatched = actions
        self.turn_ctx = ctx


def _deps(orch: _FakeOrchestrator, disp: _FakeDispatcher) -> AiReplyDeps:
    return AiReplyDeps(
        orchestrator=orch,
        dispatcher=disp,
        history=[],
        system_prompt="sei un assistente",
        hot_threshold=80,
        advance_threshold=60,
    )


async def test_ai_reply_in_window_sends_free_text() -> None:
    sender, orch, disp = _FakeSender(), _FakeOrchestrator(reply="Ciao Mario!"), _FakeDispatcher()
    ok = await _do_ai_reply(
        SimpleNamespace(node_key="a"),
        {"objective": "recupera", "window_policy": "auto"},
        _run_ctx(within_window=True),
        sender=sender,
        templates=object(),
        ai_deps=_deps(orch, disp),
    )
    assert ok is True
    assert sender.text == "Ciao Mario!"
    assert orch.called is True


async def test_ai_reply_outside_window_freeform_only_skips() -> None:
    sender, orch, disp = _FakeSender(), _FakeOrchestrator(), _FakeDispatcher()
    ok = await _do_ai_reply(
        SimpleNamespace(node_key="a"),
        {"objective": "recupera", "window_policy": "freeform_only"},
        _run_ctx(within_window=False),
        sender=sender,
        templates=object(),
        ai_deps=_deps(orch, disp),
    )
    assert ok is False
    assert sender.text is None and sender.template is None


async def test_ai_reply_skips_on_takeover_without_calling_llm() -> None:
    sender, orch, disp = _FakeSender(), _FakeOrchestrator(), _FakeDispatcher()
    ok = await _do_ai_reply(
        SimpleNamespace(node_key="a"),
        {"objective": "recupera"},
        _run_ctx(ai_paused=True),
        sender=sender,
        templates=object(),
        ai_deps=_deps(orch, disp),
    )
    assert ok is False
    assert orch.called is False  # no LLM tokens spent under human takeover
    assert sender.text is None


async def test_ai_reply_dispatches_actions_after_send() -> None:
    actions = [OrchestratorAction(kind="update_score", payload={"signals": {"has_name": True}})]
    sender, orch, disp = _FakeSender(), _FakeOrchestrator(actions=actions), _FakeDispatcher()
    run_ctx = _run_ctx(within_window=True)
    ok = await _do_ai_reply(
        SimpleNamespace(node_key="a"),
        {"objective": "qualifica", "window_policy": "auto"},
        run_ctx,
        sender=sender,
        templates=object(),
        ai_deps=_deps(orch, disp),
    )
    assert ok is True
    assert disp.dispatched == actions
    assert disp.turn_ctx.merchant_id == run_ctx.merchant_id
    assert disp.turn_ctx.conversation_id == run_ctx.conversation_id


# ---- run_proactive: action filtering + force_model -------------------------


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.model = "fake"

    async def complete(self, *, messages: list, response_format: Any = None) -> Any:
        return SimpleNamespace(
            content=self.content, model="fake", tokens_in=1, tokens_out=1, latency_ms=1
        )


class _FakeRouter:
    def __init__(self, content: str) -> None:
        self._client = _FakeClient(content)
        self.last_req: Any = None

    async def select(self, req: Any) -> Any:
        self.last_req = req
        return self._client

    async def fallback(self) -> Any:
        return None


async def test_run_proactive_filters_actions_and_forces_model() -> None:
    from ai_core.orchestrator import ConversationContext

    content = (
        '{"reply_text": "ok", "actions": ['
        '{"kind": "update_score", "payload": {}},'
        '{"kind": "book_slot", "payload": {}}]}'
    )
    router = _FakeRouter(content)
    orch = ConversationOrchestrator(router)  # type: ignore[arg-type]
    ctx = ConversationContext(
        merchant_id=uuid4(),
        tenant_id=uuid4(),
        lead_id=uuid4(),
        lead_score=10,
        hot_threshold=80,
        system_prompt="sei un assistente",
    )
    resp = await orch.run_proactive(
        ctx,
        objective="recupera il lead",
        allowed_actions={"update_score"},
        force_model="gpt-special",
    )
    # book_slot is filtered out; only the allowed action survives.
    assert [a.kind for a in resp.actions] == ["update_score"]
    assert router.last_req.force_model == "gpt-special"
