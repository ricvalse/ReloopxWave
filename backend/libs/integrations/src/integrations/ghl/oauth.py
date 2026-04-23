"""GoHighLevel OAuth2 helpers — state signing + authorization code exchange.

Flow (section 7.1):
1. Merchant clicks "Connect GHL" → backend mints a signed state tying merchant_id
   and an expiry to the caller, returns the GHL authorize URL with that state.
2. GHL redirects back to `/integrations/ghl/oauth/callback?code=...&state=...`.
3. Backend verifies the state, extracts merchant_id, exchanges the code for a
   token bundle, encrypts with the KEK, and upserts the integrations row.

Keeping state signing in-library (rather than in the router) so the
contract + rotation story lives with the provider code.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx

from shared import IntegrationError

GHL_MARKETPLACE_BASE = "https://marketplace.gohighlevel.com"
GHL_TOKEN_URL = "https://services.leadconnectorhq.com/oauth/token"  # noqa: S105 — public OAuth endpoint, not a secret

DEFAULT_SCOPES = (
    "contacts.readonly",
    "contacts.write",
    "opportunities.readonly",
    "opportunities.write",
    "calendars.readonly",
    "calendars.write",
    "calendars/events.readonly",
    "calendars/events.write",
    "locations.readonly",
    "conversations.readonly",
    "conversations.write",
    "conversations/message.readonly",
    "conversations/message.write",
)

STATE_TTL_SECONDS = 600  # 10 min — oauth round-trip must finish inside this window


# ---- State signing ----------------------------------------------------------


def sign_oauth_state(*, merchant_id: UUID, secret: str, now: int | None = None) -> str:
    """Serialize `{merchant_id, nonce, exp}` + HMAC-SHA256 → `<payload_b64>.<sig_hex>`."""
    if not secret:
        raise IntegrationError(
            "OAuth state secret not configured",
            error_code="oauth_state_secret_missing",
        )
    issued = int(now if now is not None else time.time())
    payload = {
        "m": str(merchant_id),
        "n": _rand_nonce(),
        "e": issued + STATE_TTL_SECONDS,
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _sign(payload_b64, secret)
    return f"{payload_b64}.{sig}"


@dataclass(slots=True, frozen=True)
class VerifiedState:
    merchant_id: UUID
    expires_at: int


def verify_oauth_state(state: str, *, secret: str, now: int | None = None) -> VerifiedState:
    if not secret:
        raise IntegrationError(
            "OAuth state secret not configured",
            error_code="oauth_state_secret_missing",
        )
    try:
        payload_b64, sig = state.split(".", 1)
    except ValueError as e:
        raise IntegrationError(
            "Malformed OAuth state",
            error_code="oauth_state_malformed",
        ) from e

    expected = _sign(payload_b64, secret)
    if not hmac.compare_digest(sig, expected):
        raise IntegrationError(
            "OAuth state signature mismatch",
            error_code="oauth_state_invalid",
        )

    try:
        payload = json.loads(_b64url_decode(payload_b64))
        merchant_id = UUID(payload["m"])
        expires_at = int(payload["e"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise IntegrationError(
            "OAuth state payload unreadable",
            error_code="oauth_state_payload_invalid",
        ) from e

    current = int(now if now is not None else time.time())
    if current >= expires_at:
        raise IntegrationError(
            "OAuth state expired",
            error_code="oauth_state_expired",
        )

    return VerifiedState(merchant_id=merchant_id, expires_at=expires_at)


# ---- Authorize URL + token exchange ----------------------------------------


def build_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
    }
    return f"{GHL_MARKETPLACE_BASE}/oauth/chooselocation?{urlencode(params)}"


@dataclass(slots=True, frozen=True)
class ExchangedTokens:
    access_token: str
    refresh_token: str
    expires_at: int
    location_id: str | None
    raw: dict[str, Any]


async def exchange_authorization_code(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    http: httpx.AsyncClient | None = None,
) -> ExchangedTokens:
    """POST the auth code to GHL's token endpoint. Returns a decoded bundle."""
    owns_http = http is None
    client = http or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.post(
            GHL_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "user_type": "Location",
            },
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as e:
        raise IntegrationError(
            "GHL token exchange transport failure",
            error_code="ghl_token_transport",
            reason=str(e),
        ) from e
    finally:
        if owns_http:
            await client.aclose()

    if resp.status_code >= 400:
        raise IntegrationError(
            "GHL rejected authorization code",
            error_code="ghl_token_rejected",
            status_code=resp.status_code,
            body=resp.text[:500],
        )
    data: dict[str, Any] = resp.json()

    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not access or not refresh:
        raise IntegrationError(
            "GHL token response missing tokens",
            error_code="ghl_token_incomplete",
            body=data,
        )

    # GHL returns `expires_in` (seconds). Normalize to an absolute epoch.
    expires_at = int(time.time()) + int(data.get("expires_in", 0))
    location_id = data.get("locationId") or data.get("location_id")

    return ExchangedTokens(
        access_token=str(access),
        refresh_token=str(refresh),
        expires_at=expires_at,
        location_id=str(location_id) if location_id else None,
        raw=data,
    )


# ---- Internals --------------------------------------------------------------


def _sign(payload_b64: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()


def _rand_nonce() -> str:
    import os

    return _b64url_encode(os.urandom(12))


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    # Pad to multiple of 4
    padded = s + "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))
