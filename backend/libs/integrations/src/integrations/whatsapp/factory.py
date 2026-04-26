"""WhatsApp sender factory.

Wave Marketing operates a single 360dialog Partner account; every merchant's
phone number is a channel under that one partnership. The API key lives in
the platform-level env var `WHATSAPP_D360_API_KEY` and is the same for all
merchants — the per-merchant `phone_number_id` is just the channel id we
attach to outgoing messages and route inbound webhooks by.

`build_whatsapp_sender(phone_number_id=...)` returns a ready-to-use D360
client. The api_key argument is optional and falls back to settings; pass
it explicitly only in tests that want to inject a fake.
"""
from __future__ import annotations

from typing import Any, Protocol

from integrations.whatsapp.d360_client import D360WhatsAppClient
from shared import get_settings


class WhatsAppSender(Protocol):
    async def send_text(self, *, to_phone: str, text: str) -> dict[str, Any]: ...

    async def close(self) -> None: ...


def build_whatsapp_sender(
    *, phone_number_id: str, api_key: str | None = None
) -> WhatsAppSender:
    """Return a 360dialog sender bound to the platform's shared Partner key."""
    key = api_key if api_key is not None else get_settings().whatsapp_d360_api_key
    if not key:
        raise RuntimeError(
            "WHATSAPP_D360_API_KEY is not configured — outbound WhatsApp is disabled."
        )
    return D360WhatsAppClient(api_key=key, phone_number_id=phone_number_id)
