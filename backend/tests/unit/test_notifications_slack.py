"""Unit tests for the isolated `notifications` lib (Slack channel).

Pure formatter cases + dispatch delivery via httpx.MockTransport (no real HTTP):
  - default handoff layout has a header, a fields section, and an inbox button;
  - the overdue layout shows the minutes;
  - custom_text short-circuits to plain text with placeholders substituted;
  - detail prefers the AI summary over the last message;
  - dispatch returns True on 200, False on empty URL, False on 4xx (no retry),
    and False after retrying a persistent 5xx.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from notifications import (
    KIND_HANDOFF,
    KIND_HANDOFF_OVERDUE,
    SlackNotification,
    build_slack_payload,
    send_slack_notification,
)


def _notif(**over: Any) -> SlackNotification:
    base: dict[str, Any] = {
        "kind": KIND_HANDOFF,
        "lead_name": "Mario Rossi",
        "phone": "393331112233",
        "reason": "cliente arrabbiato",
        "summary": None,
        "last_message": "voglio parlare con un umano",
        "inbox_url": "https://portal.example/conversations/abc",
    }
    base.update(over)
    return SlackNotification(**base)


# --- formatter -------------------------------------------------------------


def test_default_layout_has_header_fields_and_button() -> None:
    payload = build_slack_payload(_notif())
    assert payload["text"].startswith("🙋")
    blocks = payload["blocks"]
    assert blocks[0]["type"] == "header"
    section = next(b for b in blocks if b["type"] == "section" and "fields" in b)
    joined = " ".join(f["text"] for f in section["fields"])
    assert "Mario Rossi" in joined
    assert "393331112233" in joined
    assert "cliente arrabbiato" in joined
    actions = next(b for b in blocks if b["type"] == "actions")
    assert actions["elements"][0]["url"] == "https://portal.example/conversations/abc"


def test_overdue_layout_shows_minutes() -> None:
    payload = build_slack_payload(_notif(kind=KIND_HANDOFF_OVERDUE, overdue_minutes=42))
    assert "42" in payload["text"]
    assert payload["blocks"][0]["text"]["text"].endswith("42 min")


def test_custom_text_substitutes_placeholders_and_keeps_the_deep_link() -> None:
    payload = build_slack_payload(_notif(custom_text="Handoff {name} ({phone}): {reason}"))
    assert payload["text"] == "Handoff Mario Rossi (393331112233): cliente arrabbiato"
    # Writing your own copy must not cost the one-click way into the thread.
    actions = next(b for b in payload["blocks"] if b["type"] == "actions")
    assert actions["elements"][0]["url"] == "https://portal.example/conversations/abc"


def test_custom_text_without_deep_link_stays_plain() -> None:
    payload = build_slack_payload(_notif(custom_text="Handoff {name}", inbox_url=None))
    assert payload == {"text": "Handoff Mario Rossi"}


def test_custom_text_escapes_substituted_values_not_the_template() -> None:
    """The merchant's own mrkdwn survives; what the customer wrote does not get
    to inject link syntax or an @channel ping into an operator channel."""
    payload = build_slack_payload(
        _notif(
            custom_text="*Handoff* di {name}: {last_message}",
            lead_name="Mario",
            last_message="<https://evil.example|clicca>",
            inbox_url=None,
        )
    )
    assert payload["text"].startswith("*Handoff* di Mario")  # template formatting kept
    assert "&lt;https://evil.example" in payload["text"]
    assert "<https://evil.example|clicca>" not in payload["text"]


def test_summary_and_last_message_are_both_shown() -> None:
    """They answer different questions — why it escalated, and what the customer
    actually said. Showing only the brief hid the message it was written about."""
    payload = build_slack_payload(_notif(summary="riassunto AI", last_message="ultimo msg"))
    quotes = [
        b["text"]["text"]
        for b in payload["blocks"]
        if b["type"] == "section" and "text" in b and ">" in b["text"]["text"]
    ]
    joined = "\n".join(quotes)
    assert "Riassunto AI" in joined and "riassunto AI" in joined
    assert "Ultimo messaggio" in joined and "ultimo msg" in joined


def test_no_inbox_url_no_button() -> None:
    payload = build_slack_payload(_notif(inbox_url=None))
    assert not any(b["type"] == "actions" for b in payload["blocks"])


def test_custom_text_all_empty_placeholders_falls_back_to_blocks() -> None:
    # A custom line of only placeholders that resolve to empty would produce
    # {"text": ""}, which Slack rejects — fall back to the default Block Kit.
    payload = build_slack_payload(_notif(custom_text="{name}", lead_name=""))
    assert "blocks" in payload
    assert payload["text"] != ""


def test_untrusted_content_is_mrkdwn_escaped() -> None:
    # A customer's last message must not inject Slack link syntax `<url|text>`.
    payload = build_slack_payload(
        _notif(
            reason="a < b & c",
            summary=None,
            last_message="<https://evil.example|clicca>",
        )
    )
    section = next(b for b in payload["blocks"] if b["type"] == "section" and "fields" in b)
    reason_field = next(f["text"] for f in section["fields"] if "Motivo" in f["text"])
    assert "&lt;" in reason_field and "&amp;" in reason_field
    detail = next(
        b
        for b in payload["blocks"]
        if b["type"] == "section" and "text" in b and ">" in b["text"]["text"]
    )
    assert "&lt;https://evil.example" in detail["text"]["text"]
    assert "<https://evil.example|clicca>" not in detail["text"]["text"]


def test_fallback_text_names_the_lead() -> None:
    """The `text` field is what shows on a phone lock screen: identical for every
    alert, it forced the operator to open Slack to learn who it was about."""
    payload = build_slack_payload(_notif())
    assert "Mario Rossi" in payload["text"]


# --- dispatch --------------------------------------------------------------


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_dispatch_returns_true_on_200() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text="ok")

    async with _client(handler) as http:
        ok = await send_slack_notification(
            "https://hooks.slack.com/services/T/B/x", _notif(), http=http
        )
    assert ok is True
    assert seen["url"] == "https://hooks.slack.com/services/T/B/x"


async def test_dispatch_false_on_empty_url() -> None:
    ok = await send_slack_notification("", _notif())
    assert ok is False


async def test_dispatch_false_on_4xx_no_retry() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, text="no_service")

    async with _client(handler) as http:
        ok = await send_slack_notification("https://hooks.slack.com/x", _notif(), http=http)
    assert ok is False
    assert calls["n"] == 1  # 4xx fails fast — no retry


async def test_dispatch_false_on_persistent_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("notifications.slack_client.asyncio.sleep", no_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    async with _client(handler) as http:
        ok = await send_slack_notification("https://hooks.slack.com/x", _notif(), http=http)
    assert ok is False
    assert calls["n"] == 3  # retried up to _MAX_ATTEMPTS then gave up
