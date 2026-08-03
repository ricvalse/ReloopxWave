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
    """Fill the merchant's placeholders. The template itself keeps whatever
    mrkdwn the merchant wrote, but every *substituted value* is escaped: name,
    phone, reason and message text come from the customer or the model, and
    unescaped they can inject `<url|text>` link syntax or an `<!channel>` ping
    into an operator channel."""
    return (
        template.replace("{name}", _mrkdwn(n.lead_name or ""))
        .replace("{phone}", _mrkdwn(n.phone or ""))
        .replace("{reason}", _mrkdwn(n.reason or ""))
        .replace("{summary}", _mrkdwn(_truncate(n.summary or "", _MAX_DETAIL_LEN)))
        .replace("{last_message}", _mrkdwn(_truncate(n.last_message or "", _MAX_DETAIL_LEN)))
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
        # Merchant-authored copy: their own mrkdwn survives, the values filled
        # into it don't (see `_substitute`).
        rendered = _truncate(_substitute(n.custom_text, n), _MAX_TEXT_LEN)
        if rendered:
            payload: dict[str, Any] = {"text": rendered}
            if n.inbox_url:
                # Writing your own copy shouldn't cost you the one-click way into
                # the conversation — that button is the whole point of the alert.
                payload["blocks"] = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": rendered}},
                    _open_conversation_block(n.inbox_url),
                ]
            return payload
        # Placeholders all resolved to empty → fall through to the default layout
        # (Slack rejects a message with an empty text and no blocks).

    header = _header(n)

    fields: list[str] = []
    if n.lead_name:
        fields.append(f"*Cliente:*\n{_mrkdwn(_truncate(n.lead_name, 200))}")
    if n.phone:
        fields.append(f"*Telefono:*\n{_mrkdwn(_truncate(n.phone, 60))}")
    if n.reason:
        # The reason is LLM-written and occasionally a paragraph. Slack rejects
        # the whole message when a single field runs long, so an untruncated one
        # would drop the alert entirely rather than render it ugly.
        fields.append(f"*Motivo:*\n{_mrkdwn(_truncate(n.reason, 300))}")

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
    # Two different things an operator needs, previously mutually exclusive: the
    # AI's brief tells them *why* the thread escalated, the customer's own words
    # tell them what they are walking into. Showing only the brief hid the actual
    # message whenever the AI had written one.
    if n.summary:
        blocks.append(_quote_block("Riassunto AI", n.summary))
    if n.last_message:
        blocks.append(_quote_block("Ultimo messaggio", n.last_message))
    if n.inbox_url:
        blocks.append(_open_conversation_block(n.inbox_url))

    # `text` is the notification fallback shown in the OS/desktop notification and
    # anywhere blocks can't render — so it carries who it is about, not just the
    # header, which was identical for every alert on the phone lock screen.
    fallback = header
    who = " ".join(p for p in (n.lead_name, n.phone) if p).strip()
    if who:
        fallback = f"{header} — {_truncate(who, 120)}"
    return {"text": fallback, "blocks": blocks}


def _quote_block(label: str, body: str) -> dict[str, Any]:
    text = _mrkdwn(_truncate(body, _MAX_DETAIL_LEN))
    return {"type": "section", "text": {"type": "mrkdwn", "text": f"*{label}:*\n> {text}"}}


def _open_conversation_block(inbox_url: str) -> dict[str, Any]:
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Apri conversazione", "emoji": True},
                "url": inbox_url,
                "style": "primary",
            }
        ],
    }
