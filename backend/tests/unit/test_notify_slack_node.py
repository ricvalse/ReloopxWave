"""Automation `notify_slack` node + handoff-event wiring (unit).

Covers the thin core call sites that glue the isolated `notifications` lib to the
automation engine:
  - notify_slack no-ops when the merchant has no Slack webhook;
  - notify_slack builds a SlackNotification from the run context (default kind,
    custom text, inbox deep link) and delegates to the lib;
  - the overdue trigger picks the overdue kind + fills overdue_minutes;
  - human_handoff emits `conversation.escalated` — but not when the flow was
    itself fired by that event (anti-loop);
  - the new triggers/action are in the taxonomy and a notify_slack graph passes
    validation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from workers.automation import engine
from workers.automation.engine import EVENT_TO_TRIGGER, RunContext


def _run_ctx(**over: Any) -> RunContext:
    base: dict[str, Any] = {
        "phone": "393331112233",
        "wa_phone_number_id": "pnid",
        "within_window": True,
        "score": 50,
        "temperature": "warm",
        "name": "Mario Rossi",
        "last_message": "voglio un umano",
        "lead_id": uuid4(),
        "conversation_id": uuid4(),
        "tenant_id": uuid4(),
        "merchant_id": uuid4(),
    }
    base.update(over)
    return RunContext(**base)


def _settings() -> Any:
    return SimpleNamespace(
        integrations_kek_base64="k", public_web_merchant_url="https://portal.example"
    )


class _FakeSession:
    def __init__(self, conv: Any = None) -> None:
        self._conv = conv

    async def get(self, _model: Any, _id: Any) -> Any:
        return self._conv


def _conv(reason: str = "angry", summary: str | None = "brief", handoff_at: Any = None) -> Any:
    return SimpleNamespace(handoff_reason=reason, handoff_summary=summary, handoff_at=handoff_at)


def _node(node_type: str, config: dict | None = None) -> Any:
    return SimpleNamespace(node_key="n", kind="action", type=node_type, config=config or {})


def _patch_integrations(monkeypatch: pytest.MonkeyPatch, *, secret: Any) -> None:
    class FakeIntegRepo:
        def __init__(self, session: Any, *, kek_base64: str) -> None: ...

        async def resolve_secret(self, provider: str, merchant_id: Any) -> Any:
            assert provider == "slack"
            return secret

    monkeypatch.setattr(engine, "IntegrationRepository", FakeIntegRepo)


def _capture_sends(monkeypatch: pytest.MonkeyPatch) -> list:
    sent: list = []

    async def fake_send(url: str, notif: Any, *, http: Any = None) -> bool:
        sent.append((url, notif))
        return True

    # The engine does `from notifications import send_slack_notification` lazily,
    # so patch the attribute on the package it reads from.
    monkeypatch.setattr("notifications.send_slack_notification", fake_send)
    return sent


# --- notify_slack node -----------------------------------------------------


async def test_notify_slack_skips_without_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_integrations(monkeypatch, secret=None)
    sent = _capture_sends(monkeypatch)

    ok = await engine._do_notify_slack(
        _node("notify_slack"), {}, _run_ctx(), session=_FakeSession(), settings=_settings()
    )

    assert ok is False
    assert sent == []  # no webhook → nothing delivered


async def test_notify_slack_sends_default_kind_with_deep_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_integrations(
        monkeypatch, secret=SimpleNamespace(secret="https://hooks.slack.com/x", meta={})
    )
    sent = _capture_sends(monkeypatch)
    rc = _run_ctx()
    cfg = {"text": "Handoff {name}"}

    ok = await engine._do_notify_slack(
        _node("notify_slack", cfg), cfg, rc, session=_FakeSession(_conv()), settings=_settings()
    )

    assert ok is False  # sends no WhatsApp
    assert len(sent) == 1
    url, notif = sent[0]
    assert url == "https://hooks.slack.com/x"
    assert notif.kind == "handoff"
    assert notif.custom_text == "Handoff {name}"
    assert notif.reason == "angry"
    assert notif.inbox_url == f"https://portal.example/conversations/{rc.conversation_id}"


async def test_notify_slack_overdue_kind_and_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_integrations(
        monkeypatch, secret=SimpleNamespace(secret="https://hooks.slack.com/x", meta={})
    )
    sent = _capture_sends(monkeypatch)
    rc = _run_ctx(trigger_type="conversation_handoff_overdue")
    conv = _conv(handoff_at=datetime.now(tz=UTC) - timedelta(minutes=20))

    await engine._do_notify_slack(
        _node("notify_slack"), {}, rc, session=_FakeSession(conv), settings=_settings()
    )

    _, notif = sent[0]
    assert notif.kind == "handoff_overdue"
    assert notif.overdue_minutes is not None and notif.overdue_minutes >= 19


# --- human_handoff emit + anti-loop ----------------------------------------


def _patch_handoff_repos(
    monkeypatch: pytest.MonkeyPatch, events: list, *, claimed: bool = True
) -> None:
    class FakeConvRepo:
        def __init__(self, session: Any) -> None: ...

        async def claim_handoff(self, cid: Any, *, reason: str | None = None) -> bool:
            return claimed

    class FakeAnalytics:
        def __init__(self, session: Any) -> None: ...

        async def emit(self, **kw: Any) -> None:
            events.append(kw)

    monkeypatch.setattr(engine, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(engine, "AnalyticsRepository", FakeAnalytics)


async def test_human_handoff_emits_escalated_on_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list = []
    _patch_handoff_repos(monkeypatch, events, claimed=True)
    rc = _run_ctx(trigger_type="message_received")

    ok = await engine._do_human_handoff(
        _node("human_handoff", {"reason": "angry"}), {"reason": "angry"}, rc, session=_FakeSession()
    )

    assert ok is False
    assert rc.ai_paused is True
    assert events and events[0]["event_type"] == "conversation.escalated"
    assert events[0]["properties"]["reason"] == "angry"


async def test_human_handoff_noop_when_already_handed_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Losing the atomic claim (thread already in handoff) → no emit, no loop; the
    # bot still stays paused for downstream ai_reply nodes.
    events: list = []
    _patch_handoff_repos(monkeypatch, events, claimed=False)
    rc = _run_ctx(trigger_type="conversation_handoff_overdue")

    await engine._do_human_handoff(_node("human_handoff"), {}, rc, session=_FakeSession())

    assert events == []
    assert rc.ai_paused is True


# --- taxonomy + validation -------------------------------------------------


def test_new_triggers_and_action_in_taxonomy() -> None:
    from db.models.automation import ACTION_TYPES, TRIGGER_TYPES

    assert "conversation_escalated" in TRIGGER_TYPES
    assert "conversation_handoff_overdue" in TRIGGER_TYPES
    assert "notify_slack" in ACTION_TYPES


def test_event_to_trigger_maps_handoff_events() -> None:
    assert EVENT_TO_TRIGGER["conversation.escalated"] == "conversation_escalated"
    assert EVENT_TO_TRIGGER["conversation.handoff_overdue"] == "conversation_handoff_overdue"


def test_notify_slack_graph_passes_validation() -> None:
    from ai_core.automations import validate_graph

    nodes = [
        {"node_key": "t", "kind": "trigger", "type": "conversation_escalated", "config": {}},
        {"node_key": "a", "kind": "action", "type": "notify_slack", "config": {}},
    ]
    edges = [{"source_key": "t", "target_key": "a", "branch": "default"}]
    result = validate_graph(nodes, edges)
    assert result.ok
    assert result.trigger_type == "conversation_escalated"
