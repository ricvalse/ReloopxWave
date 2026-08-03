"""Automations must not talk over an operator who took the thread.

`ai_paused` (human takeover / soft-pause / open handoff) used to be checked only
inside `_do_ai_reply`, so a flow whose node was a plain `send` — a no-answer
nudge, a re-engagement message — still went out to a customer the operator was
actively handling. The gate now lives in `_do_action` and covers every
customer-facing node type, while internal nodes keep running (they are how the
operator learns about the thread in the first place).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from workers.automation import engine as eng
from workers.automation.engine import RunContext, _do_action


def _run_ctx(*, ai_paused: bool) -> RunContext:
    return RunContext(
        phone="393331112233",
        wa_phone_number_id="pnid",
        within_window=True,
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


def _node(node_type: str, **cfg: Any) -> SimpleNamespace:
    return SimpleNamespace(node_key=f"n-{node_type}", type=node_type, kind="action", config=cfg)


@pytest.fixture
def sends(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    recorded: list[Any] = []

    async def fake_send_proactive(sender: Any, **kw: Any) -> None:
        recorded.append(kw)

    monkeypatch.setattr(eng, "_send_proactive", fake_send_proactive)
    return recorded


@pytest.mark.parametrize("node_type", ["send", "send_template", "ai_reply"])
async def test_customer_facing_nodes_skipped_during_takeover(
    node_type: str, sends: list[Any]
) -> None:
    node = _node(node_type, free_text="Ciao, ci sei ancora?", template_id=None)

    fired = await _do_action(
        node,
        _run_ctx(ai_paused=True),
        sender=object(),
        templates=object(),
        ai_deps=None,
        session=None,
        settings=None,
    )

    assert fired is False
    assert sends == []


async def test_send_goes_out_when_the_bot_still_owns_the_thread(
    sends: list[Any],
) -> None:
    """Control: the gate is about takeover, not about muting automations."""
    node = _node("send", free_text="Ciao, ci sei ancora?")

    fired = await _do_action(
        node,
        _run_ctx(ai_paused=False),
        sender=object(),
        templates=object(),
        ai_deps=None,
        session=None,
        settings=None,
    )

    assert fired is True
    assert len(sends) == 1


async def test_notify_slack_still_runs_during_takeover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The alert that tells the operator about the handoff must not be gated by
    the handoff itself."""
    called: list[str] = []

    async def fake_notify(node: Any, cfg: Any, run_ctx: Any, **kw: Any) -> bool:
        called.append(node.node_key)
        return True

    monkeypatch.setattr(eng, "_do_notify_slack", fake_notify)

    fired = await _do_action(
        _node("notify_slack", channel="#handoff"),
        _run_ctx(ai_paused=True),
        sender=object(),
        templates=object(),
        ai_deps=None,
        session=None,
        settings=None,
    )

    assert fired is True
    assert called == ["n-notify_slack"]
