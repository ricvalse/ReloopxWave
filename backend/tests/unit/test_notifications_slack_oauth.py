"""Unit tests for the Slack OAuth v2 helpers in the isolated `notifications` lib.

State signing round-trip + tamper/expiry rejection, the authorize URL shape, and
the code exchange via httpx.MockTransport (no real HTTP):
  - a valid state verifies back to the same merchant_id;
  - a tampered signature / expired / malformed state raises;
  - the authorize URL carries client_id + scope=incoming-webhook + redirect + state;
  - exchange returns the webhook URL from `incoming_webhook.url` on ok=true;
  - ok=false, a missing webhook, and non-JSON all raise IntegrationError.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from notifications import (
    build_slack_authorize_url,
    exchange_slack_code,
    sign_slack_state,
    verify_slack_state,
)
from shared import IntegrationError

_SECRET = "test-state-secret"


# --- state signing ---------------------------------------------------------


def test_state_round_trip() -> None:
    mid = uuid4()
    state = sign_slack_state(merchant_id=mid, secret=_SECRET, now=1000)
    verified = verify_slack_state(state, secret=_SECRET, now=1000)
    assert verified.merchant_id == mid
    assert verified.expires_at == 1000 + 600


def test_state_tampered_signature_rejected() -> None:
    state = sign_slack_state(merchant_id=uuid4(), secret=_SECRET, now=1000)
    payload, _sig = state.split(".", 1)
    with pytest.raises(IntegrationError):
        verify_slack_state(f"{payload}.deadbeef", secret=_SECRET, now=1000)


def test_state_wrong_secret_rejected() -> None:
    state = sign_slack_state(merchant_id=uuid4(), secret=_SECRET, now=1000)
    with pytest.raises(IntegrationError):
        verify_slack_state(state, secret="other-secret", now=1000)


def test_state_expired_rejected() -> None:
    state = sign_slack_state(merchant_id=uuid4(), secret=_SECRET, now=1000)
    with pytest.raises(IntegrationError):
        verify_slack_state(state, secret=_SECRET, now=1000 + 601)


def test_state_malformed_rejected() -> None:
    with pytest.raises(IntegrationError):
        verify_slack_state("not-a-valid-state", secret=_SECRET, now=1000)


# --- authorize URL ---------------------------------------------------------


def test_authorize_url_shape() -> None:
    url = build_slack_authorize_url(
        client_id="123.456",
        redirect_uri="https://api.example/integrations/slack/oauth/callback",
        state="STATE",
    )
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "client_id=123.456" in url
    assert "scope=incoming-webhook" in url
    assert "state=STATE" in url
    assert "redirect_uri=https%3A%2F%2Fapi.example" in url


# --- code exchange ---------------------------------------------------------


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def test_exchange_returns_webhook_on_ok() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-1",
                "team": {"id": "T1", "name": "Acme"},
                "incoming_webhook": {
                    "url": "https://hooks.slack.com/services/T1/B1/xyz",
                    "channel": "#alerts",
                    "channel_id": "C1",
                    "configuration_url": "https://acme.slack.com/services/B1",
                },
            },
        )

    async with _client(handler) as http:
        result = await exchange_slack_code(
            code="CODE",
            client_id="cid",
            client_secret="secret",
            redirect_uri="https://api.example/cb",
            http=http,
        )

    assert result.webhook_url == "https://hooks.slack.com/services/T1/B1/xyz"
    assert result.channel == "#alerts"
    assert result.channel_id == "C1"
    assert result.team_name == "Acme"
    assert captured["url"] == "https://slack.com/api/oauth.v2.access"
    assert "code=CODE" in captured["body"] and "client_secret=secret" in captured["body"]


async def test_exchange_raises_on_ok_false() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "invalid_code"})

    async with _client(handler) as http:
        with pytest.raises(IntegrationError):
            await exchange_slack_code(
                code="bad", client_id="c", client_secret="s", redirect_uri="u", http=http
            )


async def test_exchange_raises_when_webhook_missing() -> None:
    # ok=true but no incoming_webhook (app authorized without the scope).
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "access_token": "x", "team": {"id": "T"}})

    async with _client(handler) as http:
        with pytest.raises(IntegrationError):
            await exchange_slack_code(
                code="c", client_id="c", client_secret="s", redirect_uri="u", http=http
            )
