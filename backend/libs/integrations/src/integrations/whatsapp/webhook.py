"""WhatsApp webhook payload parsing."""
from __future__ import annotations

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


@dataclass(slots=True, frozen=True)
class WhatsAppStatusEvent:
    """Outbound message status callback.

    `status` values per Meta Cloud API:
      sent       — accepted by Meta, en route to the recipient handset
      delivered  — landed on the recipient device
      read       — recipient opened the chat (only when read receipts enabled)
      failed     — terminal failure (we record the error but don't retry —
                   the D360 client already retried 3× before raising)
    """

    phone_number_id: str
    wa_message_id: str
    status: str
    recipient_phone: str | None
    timestamp_unix: int | None
    raw: dict[str, Any]


def parse_status_payload(payload: dict[str, Any]) -> list[WhatsAppStatusEvent]:
    """Pulls `statuses[]` out of Meta's webhook envelope.

    The same webhook URL receives both message inbound events (`messages[]`)
    and status callbacks (`statuses[]`); we route them separately upstream.
    """
    events: list[WhatsAppStatusEvent] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id") or ""
            for st in value.get("statuses", []) or []:
                ts = st.get("timestamp")
                events.append(
                    WhatsAppStatusEvent(
                        phone_number_id=phone_number_id,
                        wa_message_id=str(st.get("id", "") or ""),
                        status=str(st.get("status", "") or ""),
                        recipient_phone=st.get("recipient_id"),
                        timestamp_unix=int(ts) if ts is not None else None,
                        raw=st,
                    )
                )
    return events


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
