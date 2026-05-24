"""WhatsApp sender factory.

Wave Marketing reaches 360dialog through the platform-wide router. Per-merchant
channels are minted by the router during onboarding and delivered to us via the
signed `/internal/whatsapp-connected` notify — alongside the `waba_base_url` the
channel resolves to (most channels stay on `waba-v2.360dialog.io`, but the
router honors any region-specific host 360dialog assigns).

Outbound never traverses the router: each merchant talks directly to 360dialog
with its own `D360-API-Key`. So this factory just needs the per-channel key
and base URL the IntegrationRepository has on file.
"""

from __future__ import annotations

from typing import Any, Protocol

from integrations.whatsapp.d360_client import D360WhatsAppClient


class WhatsAppSender(Protocol):
    async def send_text(self, *, to_phone: str, text: str) -> dict[str, Any]: ...

    async def close(self) -> None: ...


def build_whatsapp_sender(
    *,
    phone_number_id: str,
    api_key: str,
    waba_base_url: str | None = None,
) -> WhatsAppSender:
    """Return a 360dialog sender for `phone_number_id`.

    `api_key` is the per-channel `D360-API-Key` the router delivered through
    `/internal/whatsapp-connected`. `waba_base_url` is the per-channel host the
    router also delivered — None falls back to the D360 default inside the
    client.
    """
    if not api_key:
        raise RuntimeError(
            "WhatsApp channel api_key is missing — re-run router onboarding "
            "for this merchant to receive a fresh key."
        )
    return D360WhatsAppClient(
        api_key=api_key,
        phone_number_id=phone_number_id,
        base_url=waba_base_url or None,
    )
