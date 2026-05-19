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
    """Top-level `id` is the canonical extraction target for Cloud-API channels."""
    captured: list[dict[str, Any]] = []
    response = httpx.Response(
        200,
        json={
            "id": "META-PNID-42",
            "health_status": {"can_send_messages": "AVAILABLE"},
        },
    )
    client = _build_client(captured, response=response)

    info = await client.fetch_phone_number_id()

    assert info.phone_number_id == "META-PNID-42"
    # /health_status doesn't return display_phone_number — caller falls back
    # to the MSISDN it already has from BIO callback / Partner API.
    assert info.display_phone_number is None
    assert captured[0]["method"] == "GET"
    assert captured[0]["url"] == "https://waba-v2.360dialog.io/health_status"
    assert captured[0]["headers"]["d360-api-key"] == "ch-key"


@pytest.mark.asyncio
async def test_fetch_phone_number_id_falls_back_to_entities() -> None:
    """Some payloads omit the top-level `id` and only expose it inside
    `health_status.entities[]` for the PHONE_NUMBER entity. Defensive fallback.
    """
    captured: list[dict[str, Any]] = []
    response = httpx.Response(
        200,
        json={
            "health_status": {
                "entities": [
                    {"entity_type": "WABA", "id": "WABA-1"},
                    {"entity_type": "PHONE_NUMBER", "id": "META-PNID-42"},
                ]
            }
        },
    )
    client = _build_client(captured, response=response)

    info = await client.fetch_phone_number_id()

    assert info.phone_number_id == "META-PNID-42"


@pytest.mark.asyncio
async def test_fetch_phone_number_id_missing_field_raises() -> None:
    """Neither top-level `id` nor a PHONE_NUMBER entity present."""
    captured: list[dict[str, Any]] = []
    response = httpx.Response(
        200,
        json={"health_status": {"entities": [{"entity_type": "WABA", "id": "WABA-1"}]}},
    )
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
async def test_client_honors_base_url_override() -> None:
    """If Partner Hub returns a non-default `address`, the client targets it."""
    captured: list[dict[str, Any]] = []
    response = httpx.Response(200, json={"id": "META-PNID-99"})
    http = httpx.AsyncClient(
        base_url="https://waba-eu.360dialog.io",
        transport=_transport(captured, response=response),
    )
    client = D360WhatsAppClient(
        api_key="ch-key", phone_number_id="", http=http
    )

    await client.fetch_phone_number_id()

    assert captured[0]["url"] == "https://waba-eu.360dialog.io/health_status"


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
