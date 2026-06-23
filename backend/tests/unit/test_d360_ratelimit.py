"""360dialog outbound throttling + 429/Retry-After handling (QW5)."""

from __future__ import annotations

import time

import httpx
import pytest

from integrations.whatsapp.d360_client import D360WhatsAppClient
from integrations.whatsapp.ratelimit import acquire_channel_slot, parse_retry_after_seconds


def test_parse_retry_after_delta_seconds() -> None:
    assert parse_retry_after_seconds({"Retry-After": "12"}, default=1.0) == 12.0
    assert parse_retry_after_seconds({"retry-after": "0"}, default=1.0) == 0.0


def test_parse_retry_after_absent_or_garbage_returns_default() -> None:
    assert parse_retry_after_seconds({}, default=2.5) == 2.5
    assert parse_retry_after_seconds({"Retry-After": "soon"}, default=2.5) == 2.5
    assert parse_retry_after_seconds({"Retry-After": "-5"}, default=2.5) == 2.5


async def test_acquire_channel_slot_spaces_calls() -> None:
    # Two sends on the same channel are spaced by at least the interval; a
    # different channel is independent (no wait).
    interval = 0.05
    t0 = time.monotonic()
    await acquire_channel_slot("chan-A", interval)  # first is immediate
    await acquire_channel_slot("chan-A", interval)  # second waits ~interval
    elapsed = time.monotonic() - t0
    assert elapsed >= interval * 0.8  # generous tolerance for the scheduler

    t1 = time.monotonic()
    await acquire_channel_slot("chan-B", interval)  # fresh channel, immediate
    assert time.monotonic() - t1 < interval


async def test_acquire_channel_slot_disabled_is_noop() -> None:
    t0 = time.monotonic()
    await acquire_channel_slot("chan-C", 0.0)
    await acquire_channel_slot("chan-C", 0.0)
    assert time.monotonic() - t0 < 0.02


def _client(handler) -> D360WhatsAppClient:
    transport = httpx.MockTransport(handler)
    return D360WhatsAppClient(
        api_key="k",
        phone_number_id="PNID",
        http=httpx.AsyncClient(transport=transport, base_url="https://waba-v2.360dialog.io"),
        max_messages_per_second=0,  # disable throttle for deterministic timing
    )


async def test_send_retries_on_429_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate"})
        return httpx.Response(200, json={"messages": [{"id": "wamid.ok"}]})

    client = _client(handler)
    try:
        resp = await client.send_text(to_phone="39333", text="ciao")
    finally:
        await client.close()
    assert resp["messages"][0]["id"] == "wamid.ok"
    assert calls["n"] == 2  # retried once after the 429


async def test_send_fails_fast_on_4xx_without_retry() -> None:
    from shared import IntegrationError

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad"})

    client = _client(handler)
    try:
        with pytest.raises(IntegrationError) as exc:
            await client.send_text(to_phone="39333", text="ciao")
    finally:
        await client.close()
    assert exc.value.context.get("status") == 400
    assert calls["n"] == 1  # 4xx is permanent — no retry
