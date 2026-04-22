"""WhatsApp webhook signature verification + payload parsing."""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class WhatsAppInboundEvent:
    phone_number_id: str
    from_phone: str
    message_id: str
    kind: str  # text | interactive | image | audio | location | ...
    text: str | None
    raw: dict[str, Any]


def verify_whatsapp_signature(*, app_secret: str, payload: bytes, signature_header: str) -> bool:
    """Meta sends `X-Hub-Signature-256: sha256=<hex>` computed with HMAC-SHA256."""
    if not signature_header.startswith("sha256="):
        return False
    provided = signature_header.removeprefix("sha256=")
    expected = hmac.new(app_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def parse_inbound_payload(payload: dict[str, Any]) -> list[WhatsAppInboundEvent]:
    """Pulls the `messages[]` out of Meta's nested webhook shape.

    Status-only callbacks (delivered/read) return an empty list.
    """
    events: list[WhatsAppInboundEvent] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            for msg in value.get("messages", []) or []:
                kind = msg.get("type", "unknown")
                text: str | None = None
                if kind == "text":
                    text = msg.get("text", {}).get("body")
                elif kind == "interactive":
                    interactive = msg.get("interactive", {})
                    if interactive.get("type") == "button_reply":
                        text = interactive.get("button_reply", {}).get("title")
                    elif interactive.get("type") == "list_reply":
                        text = interactive.get("list_reply", {}).get("title")
                events.append(
                    WhatsAppInboundEvent(
                        phone_number_id=phone_number_id or "",
                        from_phone=msg.get("from", ""),
                        message_id=msg.get("id", ""),
                        kind=kind,
                        text=text,
                        raw=msg,
                    )
                )
    return events
