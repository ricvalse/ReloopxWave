"""Render a :class:`SlackNotification` to an incoming-webhook JSON body.

Pure functions, no IO — cheap to unit-test. A merchant-authored ``custom_text``
short-circuits to a plain-text message (placeholders substituted); otherwise we
build a Slack Block Kit layout.
"""

from __future__ import annotations

from typing import Any

from notifications.models import KIND_HANDOFF_OVERDUE, SlackNotification

_MAX_DETAIL_LEN = 500
_MAX_TEXT_LEN = 3000


def _truncate(value: str, limit: int) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _substitute(template: str, n: SlackNotification) -> str:
    return (
        template.replace("{name}", n.lead_name or "")
        .replace("{phone}", n.phone or "")
        .replace("{reason}", n.reason or "")
        .replace("{last_message}", n.last_message or "")
    )


def _mrkdwn(value: str) -> str:
    """Escape Slack mrkdwn metacharacters. Untrusted content (a customer's last
    message, an AI-written reason) must not inject link syntax `<url|text>` or
    swallow text with stray `<`/`>`. `&` first, so the entities aren't double-escaped."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _header(n: SlackNotification) -> str:
    if n.kind == KIND_HANDOFF_OVERDUE:
        if n.overdue_minutes is not None:
            return f"⏰ Handoff in attesa da {n.overdue_minutes} min"
        return "⏰ Handoff in attesa"
    return "🙋 Un cliente ha bisogno di un operatore"


def build_slack_payload(n: SlackNotification) -> dict[str, Any]:
    """Render ``n`` to a Slack incoming-webhook body.

    Returns a dict with ``text`` (notification fallback) and, for the default
    layout, ``blocks``. When ``custom_text`` is set we emit a plain-text message.
    """
    if n.custom_text and n.custom_text.strip():
        # Merchant-authored, so not escaped (they may want their own formatting).
        rendered = _truncate(_substitute(n.custom_text, n), _MAX_TEXT_LEN)
        if rendered:
            return {"text": rendered}
        # Placeholders all resolved to empty → fall through to the default layout
        # (Slack rejects a message with an empty text and no blocks).

    header = _header(n)

    fields: list[str] = []
    if n.lead_name:
        fields.append(f"*Cliente:*\n{_mrkdwn(n.lead_name)}")
    if n.phone:
        fields.append(f"*Telefono:*\n{_mrkdwn(n.phone)}")
    if n.reason:
        fields.append(f"*Motivo:*\n{_mrkdwn(n.reason)}")

    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": header, "emoji": True}},
    ]
    if fields:
        blocks.append(
            {
                "type": "section",
                "fields": [{"type": "mrkdwn", "text": f} for f in fields[:10]],
            }
        )
    detail = n.summary or n.last_message
    if detail:
        detail_text = _mrkdwn(_truncate(detail, _MAX_DETAIL_LEN))
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"> {detail_text}"},
            }
        )
    if n.inbox_url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Apri conversazione", "emoji": True},
                        "url": n.inbox_url,
                        "style": "primary",
                    }
                ],
            }
        )

    # `text` is the notification fallback shown in the OS/desktop notification and
    # anywhere blocks can't render.
    return {"text": header, "blocks": blocks}
