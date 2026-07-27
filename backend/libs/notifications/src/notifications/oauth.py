"""Slack OAuth v2 — "Add to Slack" incoming-webhook install.

The whole point is the fewest merchant steps: with the ``incoming-webhook`` bot
scope, Slack itself asks the user which channel to post to during authorization
and hands back a ready-to-use webhook URL in the token-exchange response — no
copy-paste. Flow (mirrors the GHL OAuth pattern in ``integrations/ghl/oauth.py``,
but self-contained so this stays isolated from ``db``/``integrations``):

1. ``GET /integrations/slack/oauth/start`` (merchant-scoped) → we mint a signed
   ``state`` binding the merchant_id and return the authorize URL.
2. The browser goes to Slack, the user picks a channel + Allow.
3. Slack redirects to ``/integrations/slack/oauth/callback?code=...&state=...``.
4. We verify the state, exchange the code at ``oauth.v2.access``; the response's
   ``incoming_webhook.url`` is the webhook we store (encrypted).

State signing lives here (HMAC-SHA256 of the merchant_id + expiry), so the whole
Slack story stays in this lib — depends only on httpx + shared.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx

from shared import IntegrationError, get_logger

logger = get_logger(__name__)

SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"  # noqa: S105 — public OAuth endpoint, not a secret
DEFAULT_SCOPES = ("incoming-webhook",)
STATE_TTL_SECONDS = 600  # 10 min — the OAuth round-trip must finish inside this


# ---- State signing (merchant-bound) ----------------------------------------


def sign_slack_state(*, merchant_id: UUID, secret: str, now: int | None = None) -> str:
    """Serialize ``{merchant_id, nonce, exp}`` + HMAC-SHA256 → ``<payload_b64>.<sig_hex>``."""
    if not secret:
        raise IntegrationError(
            "Slack OAuth state secret not configured", error_code="slack_oauth_state_secret_missing"
        )
    issued = int(now if now is not None else time.time())
    payload = {"m": str(merchant_id), "n": _rand_nonce(), "e": issued + STATE_TTL_SECONDS}
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64, secret)}"


@dataclass(slots=True, frozen=True)
class VerifiedSlackState:
    merchant_id: UUID
    expires_at: int


def verify_slack_state(state: str, *, secret: str, now: int | None = None) -> VerifiedSlackState:
    if not secret:
        raise IntegrationError(
            "Slack OAuth state secret not configured", error_code="slack_oauth_state_secret_missing"
        )
    try:
        payload_b64, sig = state.split(".", 1)
    except ValueError as e:
        raise IntegrationError(
            "Malformed Slack OAuth state", error_code="slack_oauth_state_malformed"
        ) from e

    if not hmac.compare_digest(sig, _sign(payload_b64, secret)):
        raise IntegrationError(
            "Slack OAuth state signature mismatch", error_code="slack_oauth_state_invalid"
        )

    try:
        payload = json.loads(_b64url_decode(payload_b64))
        merchant_id = UUID(payload["m"])
        expires_at = int(payload["e"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise IntegrationError(
            "Slack OAuth state payload unreadable", error_code="slack_oauth_state_payload_invalid"
        ) from e

    if int(now if now is not None else time.time()) >= expires_at:
        raise IntegrationError("Slack OAuth state expired", error_code="slack_oauth_state_expired")
    return VerifiedSlackState(merchant_id=merchant_id, expires_at=expires_at)


# ---- Authorize URL + token exchange ----------------------------------------


def build_slack_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
) -> str:
    params = {
        "client_id": client_id,
        "scope": ",".join(scopes),
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{SLACK_AUTHORIZE_URL}?{urlencode(params)}"


@dataclass(slots=True, frozen=True)
class SlackOAuthResult:
    """The bits we keep from a successful ``oauth.v2.access`` exchange."""

    webhook_url: str
    channel: str | None
    channel_id: str | None
    team_id: str | None
    team_name: str | None
    raw: dict[str, Any]


async def exchange_slack_code(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    http: httpx.AsyncClient | None = None,
) -> SlackOAuthResult:
    """Exchange the auth code at ``oauth.v2.access`` and pull the webhook URL.

    Slack always replies 200 with a JSON body carrying ``ok``; a failure is
    ``{"ok": false, "error": "<code>"}``. With the ``incoming-webhook`` scope a
    success carries ``incoming_webhook.url`` — the webhook we store.
    """
    owns_http = http is None
    client = http or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.post(
            SLACK_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as e:
        raise IntegrationError(
            "Slack token exchange transport failure",
            error_code="slack_token_transport",
            reason=str(e),
        ) from e
    finally:
        if owns_http:
            await client.aclose()

    try:
        data: dict[str, Any] = resp.json()
    except ValueError as e:
        raise IntegrationError(
            "Slack token response was not JSON",
            error_code="slack_token_not_json",
            status_code=resp.status_code,
        ) from e

    if not data.get("ok"):
        raise IntegrationError(
            "Slack rejected the authorization code",
            error_code="slack_token_rejected",
            reason=str(data.get("error") or "unknown"),
        )

    webhook = data.get("incoming_webhook") or {}
    url = webhook.get("url")
    if not url:
        # Only happens if the app was authorized without the incoming-webhook
        # scope (or the user removed it on the consent screen).
        raise IntegrationError(
            "Slack response missing incoming_webhook.url",
            error_code="slack_no_webhook",
            reason="the app needs the incoming-webhook scope",
        )
    team = data.get("team") or {}
    return SlackOAuthResult(
        webhook_url=str(url),
        channel=webhook.get("channel"),
        channel_id=webhook.get("channel_id"),
        team_id=team.get("id"),
        team_name=team.get("name"),
        raw=data,
    )


# ---- Internals --------------------------------------------------------------


def _sign(payload_b64: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()


def _rand_nonce() -> str:
    return _b64url_encode(os.urandom(12))


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    padded = s + "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))
