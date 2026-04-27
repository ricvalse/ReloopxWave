"""360dialog Partner Hub client — channel administration.

Distinct from `D360WhatsAppClient`: that one talks to the WABA messaging
API with a per-channel `D360-API-Key`. This one talks to the Partner Hub
with the platform-level Partner API key (`x-api-key` header) to generate
per-channel keys and list channels.

Used by the autonomous channel-creation flow: after the merchant completes
360dialog's hosted Embedded Signup, the redirect carries a `channels=[...]`
list. Each `channel_id` gets exchanged for a real `D360-API-Key` here, and
that key is what the merchant uses for outbound + webhook configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from shared import IntegrationError, get_logger

logger = get_logger(__name__)

PARTNER_HUB_BASE = "https://hub.360dialog.io/api/v2"


@dataclass(slots=True, frozen=True)
class ChannelCredentials:
    """Result of `POST /partners/{partner_id}/channels/{channel_id}/api_keys`.

    `channel_id` is 360dialog's id for the channel — distinct from Meta's
    `phone_number_id`, which has to be fetched separately via
    `D360WhatsAppClient.fetch_phone_number_id` once we have `api_key`.
    """

    api_key: str
    channel_id: str
    waba_id: str | None
    address: str | None


@dataclass(slots=True, frozen=True)
class PartnerChannel:
    """One row from `GET /partners/{partner_id}/channels`.

    Fields are best-effort — 360dialog returns inconsistent shapes across
    their docs and dashboards. Use what's present and tolerate missing keys.
    """

    channel_id: str
    phone_number: str | None
    display_phone: str | None
    status: str | None
    raw: dict[str, Any]


class D360PartnerClient:
    def __init__(
        self,
        *,
        partner_id: str,
        partner_api_key: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if not partner_id:
            raise ValueError("partner_id is required")
        if not partner_api_key:
            raise ValueError("partner_api_key is required")
        self._partner_id = partner_id
        self._partner_api_key = partner_api_key
        self._http = http or httpx.AsyncClient(base_url=PARTNER_HUB_BASE, timeout=15.0)

    async def close(self) -> None:
        await self._http.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=5.0),
        reraise=True,
    )
    async def generate_channel_api_key(self, channel_id: str) -> ChannelCredentials:
        """Mint a `D360-API-Key` for one of the partner's channels.

        Called right after the Embedded Signup popup returns. The returned
        `api_key` is what the merchant uses for every subsequent WABA call
        (send_text, fetch_phone_number_id, configure_webhook).
        """
        path = f"/partners/{self._partner_id}/channels/{channel_id}/api_keys"
        resp = await self._http.post(path, headers=self._auth_headers())
        if resp.status_code >= 400:
            raise IntegrationError(
                f"Partner Hub rejected api_keys POST ({resp.status_code})",
                error_code="d360_partner_api_keys_failed",
                status=resp.status_code,
                body=resp.text[:500],
            )
        data: dict[str, Any] = resp.json()
        # 360dialog inconsistently names this field; amalia-ai's
        # apps/web/app/api/whatsapp/channels/route.ts:119 falls back to "key".
        api_key = data.get("api_key") or data.get("key")
        if not api_key:
            raise IntegrationError(
                "Partner Hub returned no api_key",
                error_code="d360_partner_api_keys_no_key",
                body=str(data)[:500],
            )
        return ChannelCredentials(
            api_key=api_key,
            channel_id=str(data.get("id") or channel_id),
            waba_id=data.get("app_id"),
            address=data.get("address"),
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=5.0),
        reraise=True,
    )
    async def list_channels(self) -> list[PartnerChannel]:
        """List every channel under this partner.

        Used by the refresh flow to recover from a lost `channel_id` —
        match by phone number against `setup_info.phone_number` or the
        top-level `phone_number` field.
        """
        path = f"/partners/{self._partner_id}/channels"
        resp = await self._http.get(path, headers=self._auth_headers())
        if resp.status_code >= 400:
            raise IntegrationError(
                f"Partner Hub list channels failed ({resp.status_code})",
                error_code="d360_partner_list_failed",
                status=resp.status_code,
                body=resp.text[:500],
            )
        data: Any = resp.json()
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = (
                data.get("partner_channels")
                or (data.get("partner") or {}).get("channels")
                or data.get("channels")
                or []
            )
        else:
            items = []
        return [_channel_from_dict(item) for item in items]

    def _auth_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._partner_api_key,
            "Content-Type": "application/json",
        }


def _channel_from_dict(item: dict[str, Any]) -> PartnerChannel:
    setup_info = item.get("setup_info") or {}
    phone_number = (
        setup_info.get("phone_number")
        or item.get("phone_number")
        or item.get("display_phone_number")
    )
    return PartnerChannel(
        channel_id=str(item.get("id") or ""),
        phone_number=phone_number,
        display_phone=item.get("display_phone_number") or item.get("display_phone"),
        status=item.get("status"),
        raw=dict(item),
    )
