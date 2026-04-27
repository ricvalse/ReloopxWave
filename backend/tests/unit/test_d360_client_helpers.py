"""D360WhatsAppClient onboarding helpers — fetch_phone_number_id + configure_webhook.

These two run during the autonomous channel-creation flow, between
`D360PartnerClient.generate_channel_api_key` (which gives us the
per-channel D360 key) and the final `IntegrationRepository.upsert_whatsapp`
write. Lock the wire shape so a regression here surfaces during onboarding.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from integrations.whatsapp.d360_client import D360WhatsAppClient
from shared import IntegrationError


def _transport(
    captured: list[dict[str, Any]], *, response: httpx.Response
) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": json.loads(request.content.decode() or "{}")
                if request.content
                else None,
            }
        )
        return response

    return httpx.MockTransport(handler)


def _build_client(
    captured: list[dict[str, Any]], *, response: httpx.Response
) -> D360WhatsAppClient:
    http = httpx.AsyncClient(
        base_url="https://waba-v2.360dialog.io",
        transport=_transport(captured, response=response),
    )
    return D360WhatsAppClient(api_key="ch-key", phone_number_id="", http=http)


@pytest.mark.asyncio
async def test_fetch_phone_number_id_happy_path() -> None:
    captured: list[dict[str, Any]] = []
    response = httpx.Response(
        200,
        json={"id": "META-PNID-42", "display_phone_number": "+39 000 111 222"},
    )
    client = _build_client(captured, response=response)

    info = await client.fetch_phone_number_id()

    assert info.phone_number_id == "META-PNID-42"
    assert info.display_phone_number == "+39 000 111 222"
    assert captured[0]["method"] == "GET"
    assert captured[0]["url"] == "https://waba-v2.360dialog.io/v1/configs/phone_number"
    assert captured[0]["headers"]["d360-api-key"] == "ch-key"


@pytest.mark.asyncio
async def test_fetch_phone_number_id_missing_field_raises() -> None:
    captured: list[dict[str, Any]] = []
    response = httpx.Response(200, json={"display_phone_number": "+39 000 111 222"})
    client = _build_client(captured, response=response)

    with pytest.raises(IntegrationError) as excinfo:
        await client.fetch_phone_number_id()
    assert excinfo.value.error_code == "d360_phone_number_missing"


@pytest.mark.asyncio
async def test_fetch_phone_number_id_4xx_raises() -> None:
    captured: list[dict[str, Any]] = []
    response = httpx.Response(401, text="unauthorized")
    client = _build_client(captured, response=response)

    with pytest.raises(IntegrationError) as excinfo:
        await client.fetch_phone_number_id()
    assert excinfo.value.error_code == "d360_phone_number_failed"


@pytest.mark.asyncio
async def test_configure_webhook_sends_url() -> None:
    captured: list[dict[str, Any]] = []
    response = httpx.Response(200, json={"webhook_id": "wh-1"})
    client = _build_client(captured, response=response)

    await client.configure_webhook("https://api.example.com/webhooks/whatsapp/PNID-42")

    assert captured[0]["method"] == "POST"
    assert captured[0]["url"] == "https://waba-v2.360dialog.io/configs/webhook"
    assert captured[0]["body"] == {
        "url": "https://api.example.com/webhooks/whatsapp/PNID-42"
    }
    assert captured[0]["headers"]["d360-api-key"] == "ch-key"


@pytest.mark.asyncio
async def test_configure_webhook_4xx_raises() -> None:
    captured: list[dict[str, Any]] = []
    response = httpx.Response(400, text="bad url")
    client = _build_client(captured, response=response)

    with pytest.raises(IntegrationError) as excinfo:
        await client.configure_webhook("https://api.example.com/x")
    assert excinfo.value.error_code == "d360_webhook_config_failed"
