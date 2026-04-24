"""360dialog WhatsApp Cloud API client.

360dialog is a BSP that proxies Meta Cloud API. The message payload shape is
identical to Meta's, which means the handler layer can treat the two clients
as interchangeable once we abstract the auth + base-url difference.

Auth: `D360-API-KEY: <api_key>` header (not Bearer).
Base URL: https://waba-v2.360dialog.io
Send endpoint: `/messages` (the API key already scopes to a channel, so
there's no `{phone_number_id}` path component like Meta's).
"""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from shared import IntegrationError, get_logger

logger = get_logger(__name__)

D360_BASE = "https://waba-v2.360dialog.io"


class D360WhatsAppClient:
    """Drop-in for WhatsAppClient — same method signatures, different wire."""

    def __init__(
        self,
        *,
        api_key: str,
        phone_number_id: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        # phone_number_id is stored alongside the api_key for cross-provider
        # compatibility with the Meta client even though D360 doesn't need it
        # in the request path — handlers index integrations by phone_number_id.
        self._phone_number_id = phone_number_id
        self._http = http or httpx.AsyncClient(base_url=D360_BASE, timeout=15.0)

    async def close(self) -> None:
        await self._http.aclose()

    async def send_text(self, *, to_phone: str, text: str) -> dict[str, Any]:
        return await self._send({
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": text, "preview_url": False},
        })

    async def send_template(
        self,
        *,
        to_phone: str,
        template_name: str,
        language: str = "it",
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return await self._send({
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
                "components": components or [],
            },
        })

    async def send_interactive(
        self,
        *,
        to_phone: str,
        header: str | None,
        body: str,
        buttons: list[dict[str, str]],
    ) -> dict[str, Any]:
        return await self._send({
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "header": {"type": "text", "text": header} if header else None,
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                        for b in buttons
                    ]
                },
            },
        })

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=5.0),
        reraise=True,
    )
    async def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = await self._http.post(
            "/messages",
            json={k: v for k, v in payload.items() if v is not None},
            headers={"D360-API-KEY": self._api_key},
        )
        if resp.status_code >= 400:
            raise IntegrationError(
                f"360dialog send failed ({resp.status_code})",
                error_code="d360_send_failed",
                status=resp.status_code,
                body=resp.text[:500],
            )
        result: dict[str, Any] = resp.json()
        return result
