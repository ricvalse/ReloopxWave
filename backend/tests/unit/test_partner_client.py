"""D360PartnerClient — Partner Hub admin API.

Channel-creation flow depends on this; lock the wire shape (URL path,
auth header, response field fallbacks) so a 360dialog response shape
change is caught here, not in production.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from integrations import D360PartnerClient
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
            }
        )
        return response

    return httpx.MockTransport(handler)


def _build_client(
    captured: list[dict[str, Any]], *, response: httpx.Response
) -> D360PartnerClient:
    http = httpx.AsyncClient(
        base_url="https://hub.360dialog.io/api/v2",
        transport=_transport(captured, response=response),
    )
    return D360PartnerClient(
        partner_id="PARTNER-1",
        partner_api_key="partner-key-xyz",
        http=http,
    )


@pytest.mark.asyncio
async def test_generate_channel_api_key_happy_path() -> None:
    captured: list[dict[str, Any]] = []
    response = httpx.Response(
        200,
        json={
            "api_key": "channel-key-abc",
            "id": "CH-42",
            "address": "https://waba-v2.360dialog.io",
            "app_id": "WABA-99",
        },
    )
    client = _build_client(captured, response=response)

    creds = await client.generate_channel_api_key("CH-42")

    assert creds.api_key == "channel-key-abc"
    assert creds.channel_id == "CH-42"
    assert creds.waba_id == "WABA-99"
    assert creds.address == "https://waba-v2.360dialog.io"

    assert len(captured) == 1
    call = captured[0]
    assert call["method"] == "POST"
    assert (
        call["url"]
        == "https://hub.360dialog.io/api/v2/partners/PARTNER-1/channels/CH-42/api_keys"
    )
    # Partner Hub uses the lower-case `x-api-key` header, NOT `D360-API-Key`
    # (which is for the per-channel WABA API). The two are different APIs
    # with different auth schemes; mixing them up returns 401.
    assert call["headers"]["x-api-key"] == "partner-key-xyz"


@pytest.mark.asyncio
async def test_generate_channel_api_key_falls_back_to_key_field() -> None:
    captured: list[dict[str, Any]] = []
    # Real-world quirk in 360dialog responses (amalia-ai hits this at
    # apps/web/app/api/whatsapp/channels/route.ts:119): some payloads use
    # `key` instead of `api_key`. The client tolerates both.
    response = httpx.Response(200, json={"key": "channel-key-abc", "id": "CH-42"})
    client = _build_client(captured, response=response)

    creds = await client.generate_channel_api_key("CH-42")
    assert creds.api_key == "channel-key-abc"


@pytest.mark.asyncio
async def test_generate_channel_api_key_4xx_raises_integration_error() -> None:
    captured: list[dict[str, Any]] = []
    response = httpx.Response(401, text='{"error":"invalid partner key"}')
    client = _build_client(captured, response=response)

    with pytest.raises(IntegrationError) as excinfo:
        await client.generate_channel_api_key("CH-42")
    assert excinfo.value.error_code == "d360_partner_api_keys_failed"


@pytest.mark.asyncio
async def test_generate_channel_api_key_no_key_in_body() -> None:
    captured: list[dict[str, Any]] = []
    response = httpx.Response(200, json={"id": "CH-42"})  # no api_key/key
    client = _build_client(captured, response=response)

    with pytest.raises(IntegrationError) as excinfo:
        await client.generate_channel_api_key("CH-42")
    assert excinfo.value.error_code == "d360_partner_api_keys_no_key"


@pytest.mark.asyncio
async def test_list_channels_handles_partner_channels_envelope() -> None:
    captured: list[dict[str, Any]] = []
    response = httpx.Response(
        200,
        json={
            "partner_channels": [
                {
                    "id": "CH-1",
                    "setup_info": {"phone_number": "+39000111222"},
                    "display_phone_number": "+39 000 111 222",
                    "status": "active",
                },
                {"id": "CH-2", "phone_number": "+39000333444"},
            ]
        },
    )
    client = _build_client(captured, response=response)

    channels = await client.list_channels()

    assert len(channels) == 2
    assert channels[0].channel_id == "CH-1"
    assert channels[0].phone_number == "+39000111222"
    assert channels[0].display_phone == "+39 000 111 222"
    assert channels[0].status == "active"
    assert channels[1].channel_id == "CH-2"
    assert channels[1].phone_number == "+39000333444"

    call = captured[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://hub.360dialog.io/api/v2/partners/PARTNER-1/channels"


@pytest.mark.asyncio
async def test_list_channels_handles_bare_array() -> None:
    # Some 360dialog endpoints return a bare array. Tolerate it.
    captured: list[dict[str, Any]] = []
    response = httpx.Response(200, json=[{"id": "CH-1"}])
    client = _build_client(captured, response=response)

    channels = await client.list_channels()
    assert [c.channel_id for c in channels] == ["CH-1"]


def test_construction_rejects_empty_credentials() -> None:
    with pytest.raises(ValueError):
        D360PartnerClient(partner_id="", partner_api_key="x")
    with pytest.raises(ValueError):
        D360PartnerClient(partner_id="x", partner_api_key="")
