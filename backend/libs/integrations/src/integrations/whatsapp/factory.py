"""WhatsApp sender factory.

Wave Marketing operates a single 360dialog Partner; each merchant's phone
number is a channel under it. Two routes for getting an API key:

  - **Autonomous flow (default)** — each merchant has its own per-channel
    `D360-API-Key` minted by the Partner Hub during onboarding, stored
    encrypted in `integrations.secret_ciphertext`. Callers pass it in via
    `api_key=`.
  - **Legacy manual-paste flow** — older rows have the placeholder string
    `"d360-shared-channel"` instead of a real key. We recognise that and
    fall back to the platform-level `WHATSAPP_PARTNER_API_KEY` so those
    merchants keep working until they re-onboard.
"""
from __future__ import annotations

from typing import Any, Protocol

from integrations.whatsapp.d360_client import D360WhatsAppClient
from shared import get_settings

PLACEHOLDER_API_KEY = "d360-shared-channel"


class WhatsAppSender(Protocol):
    async def send_text(self, *, to_phone: str, text: str) -> dict[str, Any]: ...

    async def close(self) -> None: ...


def build_whatsapp_sender(
    *, phone_number_id: str, api_key: str | None = None
) -> WhatsAppSender:
    """Return a 360dialog sender for `phone_number_id`.

    `api_key`: per-channel D360 key resolved from the integrations row. If
    None or the legacy placeholder, falls back to the platform Partner key.
    """
    settings = get_settings()
    key = api_key
    if not key or key == PLACEHOLDER_API_KEY:
        key = settings.whatsapp_partner_api_key
    if not key:
        raise RuntimeError(
            "WHATSAPP_PARTNER_API_KEY is not configured — outbound WhatsApp is disabled."
        )
    return D360WhatsAppClient(api_key=key, phone_number_id=phone_number_id)
