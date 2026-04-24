"""Provider-agnostic WhatsApp sender.

Callers (conversation service, follow-up scheduler, booking confirmation) want
one interface: "send a text to this phone". The concrete client depends on
whether the merchant integrated via Meta Cloud API directly or via 360dialog.
`build_whatsapp_sender` reads the provider off the resolved integration and
returns the right client, wrapped behind the `WhatsAppSender` protocol.

Unknown providers fall back to Meta to preserve pre-d360 behaviour.
"""
from __future__ import annotations

from typing import Any, Protocol

from integrations.whatsapp.client import WhatsAppClient
from integrations.whatsapp.d360_client import D360WhatsAppClient


class WhatsAppSender(Protocol):
    async def send_text(self, *, to_phone: str, text: str) -> dict[str, Any]: ...

    async def close(self) -> None: ...


def build_whatsapp_sender(
    *, provider: str | None, access_token: str, phone_number_id: str
) -> WhatsAppSender:
    """Return a sender for the given provider.

    Accepted values: "meta" (default), "d360".
    `access_token` is the Meta bearer for "meta" and the D360 API key for
    "d360" — both are stored in the same encrypted column on the integration
    row, the meta JSONB's `provider` decides how to use it.
    """
    if (provider or "meta").lower() == "d360":
        return D360WhatsAppClient(api_key=access_token, phone_number_id=phone_number_id)
    return WhatsAppClient(access_token=access_token, phone_number_id=phone_number_id)
