"""Unit tests for the automation engine graph walk (`_walk` / `_do_action`).

Pure-logic coverage with a fake sender + fake templates repo (no DB, no LLM,
no network):
  - a condition node steers the walk down the true vs false branch;
  - a `wait` node defers (minutes, successors) and stops its branch;
  - `send_message` is skipped outside the 24h window, sent inside it;
  - `send_template` is skipped when the template is missing / not approved, sent
    when approved;
  - the dispatcher's EVENT_TO_TRIGGER mapping maps the V1 analytics events.

Complements test_automation_engine_ai_reply.py (which covers the ai_reply node).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from workers.automation.engine import EVENT_TO_TRIGGER, RunContext, _do_action, _walk


def _run_ctx(*, within_window: bool = True, score: int = 50) -> RunContext:
    return RunContext(
        phone="393331112233",
        wa_phone_number_id="pnid",
        within_window=within_window,
        score=score,
        temperature="warm",
        name="Mario Rossi",
        last_message="quanto costa?",
        lead_id=uuid4(),
        conversation_id=uuid4(),
        tenant_id=uuid4(),
        merchant_id=uuid4(),
    )


class _FakeSender:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.templates: list[str] = []

    async def send_text(self, *, to_phone: str, text: str) -> dict[str, Any]:
        self.texts.append(text)
        return {"messages": [{"id": "wamid.text"}]}

    async def send_template(
        self, *, to_phone: str, template_name: str, language: str, components: list
    ) -> dict[str, Any]:
        self.templates.append(template_name)
        return {"messages": [{"id": "wamid.tpl"}]}


class _FakeTemplates:
    """Stands in for WhatsAppTemplateRepository.get()."""

    def __init__(self, template: Any = None) -> None:
        self._template = template

    async def get(self, _id: Any) -> Any:
        return self._template


def _node(node_key: str, kind: str, ntype: str, config: dict | None = None) -> Any:
    return SimpleNamespace(node_key=node_key, kind=kind, type=ntype, config=config or {})


def _automation(nodes: list[Any], edges: list[Any]) -> Any:
    return SimpleNamespace(nodes=nodes, edges=edges)


def _edge(source: str, target: str, branch: str = "default") -> Any:
    return SimpleNamespace(source_key=source, target_key=target, branch=branch)


# --- _walk: condition branching --------------------------------------------


async def test_walk_condition_follows_true_branch() -> None:
    """score>=40 (warm) → lead_score >= 40 condition passes → true branch sends."""
    automation = _automation(
        nodes=[
            _node("t", "trigger", "message_received"),
            _node("c", "condition", "lead_score", {"op": ">=", "value": 40}),
            _node("yes", "action", "send_message", {"text": "sei caldo"}),
            _node("no", "action", "send_message", {"text": "sei freddo"}),
        ],
        edges=[
            _edge("t", "c"),
            _edge("c", "yes", branch="true"),
            _edge("c", "no", branch="false"),
        ],
    )
    sender = _FakeSender()
    sent, deferrals = await _walk(
        automation,
        _run_ctx(within_window=True, score=50),
        start_keys=["c"],
        sender=sender,
        templates=_FakeTemplates(),
    )
    assert sender.texts == ["sei caldo"]
    assert sent == 1
    assert deferrals == []


async def test_walk_condition_follows_false_branch() -> None:
    automation = _automation(
        nodes=[
            _node("c", "condition", "lead_score", {"op": ">=", "value": 40}),
            _node("yes", "action", "send_message", {"text": "sei caldo"}),
            _node("no", "action", "send_message", {"text": "sei freddo"}),
        ],
        edges=[
            _edge("c", "yes", branch="true"),
            _edge("c", "no", branch="false"),
        ],
    )
    sender = _FakeSender()
    sent, _ = await _walk(
        automation,
        _run_ctx(within_window=True, score=10),  # cold → condition fails
        start_keys=["c"],
        sender=sender,
        templates=_FakeTemplates(),
    )
    assert sender.texts == ["sei freddo"]
    assert sent == 1


# --- _walk: wait deferral ---------------------------------------------------


async def test_walk_wait_defers_and_stops_branch() -> None:
    """A wait node returns (minutes, successors) and does NOT walk past it now."""
    automation = _automation(
        nodes=[
            _node("w", "action", "wait", {"minutes": 30}),
            _node("after", "action", "send_message", {"text": "dopo l'attesa"}),
        ],
        edges=[_edge("w", "after")],
    )
    sender = _FakeSender()
    sent, deferrals = await _walk(
        automation,
        _run_ctx(within_window=True),
        start_keys=["w"],
        sender=sender,
        templates=_FakeTemplates(),
    )
    # Nothing sent yet; the continuation is deferred with the successor keys.
    assert sender.texts == []
    assert sent == 0
    assert deferrals == [(30, ["after"])]


async def test_walk_wait_zero_minutes_does_not_defer() -> None:
    automation = _automation(
        nodes=[
            _node("w", "action", "wait", {"minutes": 0}),
            _node("after", "action", "send_message", {"text": "x"}),
        ],
        edges=[_edge("w", "after")],
    )
    sent, deferrals = await _walk(
        automation,
        _run_ctx(),
        start_keys=["w"],
        sender=_FakeSender(),
        templates=_FakeTemplates(),
    )
    assert deferrals == []
    assert sent == 0


# --- _do_action: send_message 24h window ------------------------------------


async def test_send_message_skipped_outside_window() -> None:
    sender = _FakeSender()
    ok = await _do_action(
        _node("a", "action", "send_message", {"text": "ciao {name}"}),
        _run_ctx(within_window=False),
        sender=sender,
        templates=_FakeTemplates(),
    )
    assert ok is False
    assert sender.texts == []


async def test_send_message_sent_inside_window_interpolates_name() -> None:
    sender = _FakeSender()
    ok = await _do_action(
        _node("a", "action", "send_message", {"text": "ciao {name}"}),
        _run_ctx(within_window=True),
        sender=sender,
        templates=_FakeTemplates(),
    )
    assert ok is True
    assert sender.texts == ["ciao Mario Rossi"]


# --- _do_action: send_template approval gate --------------------------------


async def test_send_template_skipped_when_not_approved() -> None:
    tpl = SimpleNamespace(
        name="promo",
        status="pending",
        language="it",
        variables=[],
        header_type="NONE",
        header_image_url=None,
    )
    sender = _FakeSender()
    ok = await _do_action(
        _node("a", "action", "send_template", {"template_id": str(uuid4())}),
        _run_ctx(within_window=False),  # templates allowed even outside window
        sender=sender,
        templates=_FakeTemplates(tpl),
    )
    assert ok is False
    assert sender.templates == []


async def test_send_template_skipped_when_missing() -> None:
    sender = _FakeSender()
    ok = await _do_action(
        _node("a", "action", "send_template", {"template_id": str(uuid4())}),
        _run_ctx(),
        sender=sender,
        templates=_FakeTemplates(None),
    )
    assert ok is False
    assert sender.templates == []


async def test_send_template_sent_when_approved() -> None:
    tpl = SimpleNamespace(
        name="promo",
        status="approved",
        language="it",
        variables=[],
        header_type="NONE",
        header_image_url=None,
    )
    sender = _FakeSender()
    ok = await _do_action(
        _node("a", "action", "send_template", {"template_id": str(uuid4())}),
        _run_ctx(within_window=False),
        sender=sender,
        templates=_FakeTemplates(tpl),
    )
    assert ok is True
    assert sender.templates == ["promo"]


# --- dispatcher mapping -----------------------------------------------------


def test_event_to_trigger_mapping_covers_v1_surface() -> None:
    assert EVENT_TO_TRIGGER["message.received"] == "message_received"
    assert EVENT_TO_TRIGGER["booking.created"] == "booking_created"
    assert EVENT_TO_TRIGGER["booking.failed"] == "booking_failed"
    # The no-answer follow-up firing means the lead stayed silent.
    assert EVENT_TO_TRIGGER["reminder.sent"] == "no_answer"
    # The dormant scan firing for a lead maps to lead_dormant.
    assert EVENT_TO_TRIGGER["lead_reactivation.sent"] == "lead_dormant"
    # An unmapped event is simply not a key (dispatcher filters via `in`).
    assert "objection.extracted" not in EVENT_TO_TRIGGER
