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
class WhatsAppPhoneEchoEvent:
    """Outbound message sent from the merchant's WhatsApp Business App.

    Delivered to the same webhook URL as inbound, but under a separate
    `change.field = "smb_message_echoes"` envelope, with the payload at
    `value.message_echoes[]`. `from` carries the business number, `to` the
    customer — opposite of an inbound event. Only emitted on phone numbers
    onboarded in 360dialog Coexistence mode.
    """

    phone_number_id: str
    business_phone: str  # message_echoes[].from — the merchant's WA number
    customer_phone: str  # message_echoes[].to — the conversation peer
    message_id: str
    kind: str  # text | image | audio | …
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
                   the D360 client already retried 3x before raising)
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

    Status-only callbacks (delivered/read) and Coexistence echo envelopes
    (`field='smb_message_echoes'`) return an empty list — those are parsed
    by `parse_status_payload` and `parse_message_echo_payload` respectively.
    """
    events: list[WhatsAppInboundEvent] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") == "smb_message_echoes":
                continue
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


def parse_message_echo_payload(payload: dict[str, Any]) -> list[WhatsAppPhoneEchoEvent]:
    """Pulls `message_echoes[]` (Coexistence) out of Meta's webhook envelope.

    Echo events are wrapped in a `change.field='smb_message_echoes'` entry — we
    intentionally key off the field name rather than just probing for the array,
    so a regular inbound payload that happens to include an empty echoes array
    can never be misread as outbound.
    """
    events: list[WhatsAppPhoneEchoEvent] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "smb_message_echoes":
                continue
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id") or ""
            for msg in value.get("message_echoes", []) or []:
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
                    WhatsAppPhoneEchoEvent(
                        phone_number_id=phone_number_id,
                        business_phone=str(msg.get("from", "") or ""),
                        customer_phone=str(msg.get("to", "") or ""),
                        message_id=str(msg.get("id", "") or ""),
                        kind=kind,
                        text=text,
                        raw=msg,
                    )
                )
    return events
