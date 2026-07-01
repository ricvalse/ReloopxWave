"""GoHighLevel OAuth2 helpers — state signing + authorization code exchange.

Marketplace agency-install flow (ADR 0007):
1. An agency admin clicks "Collega Agenzia GHL" in web-admin → backend mints a
   signed state tying the `tenant_id` and an expiry to the caller, returns the
   GHL authorize URL with that state.
2. GHL redirects back to `/integrations/crm/oauth/callback?code=...&state=...`
   (path avoids the "ghl" substring — GHL rejects branded redirect URIs).
3. Backend verifies the state, extracts `tenant_id`, exchanges the code with
   `user_type="Company"` for an Agency token, encrypts it with the KEK, and
   stores the `ghl_agency_installs` row.
4. GHL fires an INSTALL webhook per selected sub-account; the worker mints a
   per-location token via `mint_location_token` using the Agency token.

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
GHL_LOCATION_TOKEN_URL = "https://services.leadconnectorhq.com/oauth/locationToken"  # noqa: S105 — public OAuth endpoint, not a secret

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
    # REQUIRED to mint per-location tokens from the agency token via
    # POST /oauth/locationToken. Without these GHL rejects the mint with
    # 401 "The token is not authorized for this scope" — the location then
    # stays pending_link forever (no token → can't promote to active) and its
    # name never resolves. Must also be enabled on the GHL Marketplace app,
    # and the agency must re-authorize to obtain a token carrying them.
    "oauth.readonly",
    "oauth.write",
    "conversations.readonly",
    "conversations.write",
    "conversations/message.readonly",
    "conversations/message.write",
)

STATE_TTL_SECONDS = 600  # 10 min — oauth round-trip must finish inside this window


# ---- State signing ----------------------------------------------------------


def sign_oauth_state(*, tenant_id: UUID, secret: str, now: int | None = None) -> str:
    """Serialize `{tenant_id, nonce, exp}` + HMAC-SHA256 → `<payload_b64>.<sig_hex>`."""
    if not secret:
        raise IntegrationError(
            "OAuth state secret not configured",
            error_code="oauth_state_secret_missing",
        )
    issued = int(now if now is not None else time.time())
    payload = {
        "t": str(tenant_id),
        "n": _rand_nonce(),
        "e": issued + STATE_TTL_SECONDS,
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _sign(payload_b64, secret)
    return f"{payload_b64}.{sig}"


@dataclass(slots=True, frozen=True)
class VerifiedState:
    tenant_id: UUID
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
        tenant_id = UUID(payload["t"])
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

    return VerifiedState(tenant_id=tenant_id, expires_at=expires_at)


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
    company_id: str | None
    user_type: str | None
    raw: dict[str, Any]


async def exchange_authorization_code(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    user_type: str = "Company",
    http: httpx.AsyncClient | None = None,
) -> ExchangedTokens:
    """POST the auth code to GHL's token endpoint. Returns a decoded bundle.

    `user_type="Company"` yields an Agency-level token (marketplace agency
    install); `"Location"` yields a sub-account token.
    """
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
                "user_type": user_type,
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
    company_id = data.get("companyId") or data.get("company_id")
    resp_user_type = data.get("userType") or data.get("user_type")

    return ExchangedTokens(
        access_token=str(access),
        refresh_token=str(refresh),
        expires_at=expires_at,
        location_id=str(location_id) if location_id else None,
        company_id=str(company_id) if company_id else None,
        user_type=str(resp_user_type) if resp_user_type else None,
        raw=data,
    )


@dataclass(slots=True, frozen=True)
class MintedLocationToken:
    access_token: str
    refresh_token: str
    expires_at: int
    location_id: str
    company_id: str
    raw: dict[str, Any]


async def mint_location_token(
    *,
    agency_access_token: str,
    company_id: str,
    location_id: str,
    http: httpx.AsyncClient | None = None,
) -> MintedLocationToken:
    """Mint a Location-level token from an Agency token.

    `POST /oauth/locationToken` with the agency bearer token and the
    `companyId`/`locationId` pair. 401/403 means the agency token is stale — the
    caller should refresh it and retry. Returns a sub-account token bundle.
    """
    owns_http = http is None
    client = http or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.post(
            GHL_LOCATION_TOKEN_URL,
            data={"companyId": company_id, "locationId": location_id},
            headers={
                "Authorization": f"Bearer {agency_access_token}",
                "Version": "2021-07-28",
                "Accept": "application/json",
            },
        )
    except httpx.HTTPError as e:
        raise IntegrationError(
            "GHL location token transport failure",
            error_code="ghl_location_mint_transport",
            reason=str(e),
        ) from e
    finally:
        if owns_http:
            await client.aclose()

    if resp.status_code >= 400:
        raise IntegrationError(
            "GHL rejected location token mint",
            error_code="ghl_location_mint_rejected",
            status_code=resp.status_code,
            body=resp.text[:500],
        )
    data: dict[str, Any] = resp.json()

    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not access or not refresh:
        raise IntegrationError(
            "GHL location token response missing tokens",
            error_code="ghl_location_mint_incomplete",
            body=data,
        )

    expires_at = int(time.time()) + int(data.get("expires_in", 0))
    resp_location = data.get("locationId") or data.get("location_id") or location_id
    resp_company = data.get("companyId") or data.get("company_id") or company_id

    return MintedLocationToken(
        access_token=str(access),
        refresh_token=str(refresh),
        expires_at=expires_at,
        location_id=str(resp_location),
        company_id=str(resp_company),
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
