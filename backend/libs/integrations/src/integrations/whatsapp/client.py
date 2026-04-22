"""WhatsApp Cloud API (Meta) client — send-side only. Inbound lives in webhook.py."""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from shared import IntegrationError, get_logger

logger = get_logger(__name__)

WHATSAPP_BASE = "https://graph.facebook.com/v21.0"


class WhatsAppClient:
    def __init__(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._access_token = access_token
        self._phone_number_id = phone_number_id
        self._http = http or httpx.AsyncClient(base_url=WHATSAPP_BASE, timeout=15.0)

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
            f"/{self._phone_number_id}/messages",
            json={k: v for k, v in payload.items() if v is not None},
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        if resp.status_code >= 400:
            raise IntegrationError(
                f"WhatsApp send failed ({resp.status_code})",
                error_code="whatsapp_send_failed",
                status=resp.status_code,
                body=resp.text[:500],
            )
        return resp.json()
