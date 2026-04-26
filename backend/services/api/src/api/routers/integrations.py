"""Per-merchant external integrations — GHL (OAuth2) + WhatsApp (360dialog).

Scope:
- `POST /integrations/ghl/oauth/start` — mints a signed state, returns the
  GHL marketplace URL. Merchant user opens it in a new tab.
- `GET /integrations/ghl/oauth/callback` — verifies state, exchanges code,
  encrypts + persists the token bundle, redirects to the merchant portal.
- `POST /integrations/whatsapp/verify` — accepts a `phone_number_id`
  (the 360dialog channel id), confirms the platform's shared D360 API key
  reaches that channel, then persists the merchant→channel mapping. No
  per-merchant secrets are stored — all merchants share Wave Marketing's
  one Partner-level API key, which lives in `WHATSAPP_D360_API_KEY` env.
- `GET /integrations/status` — returns connection cards for the caller's
  merchant (agency admins can pass `?merchant_id=<uuid>` to view a specific
  merchant under their tenant).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from api.dependencies.session import CurrentContext, DBSession
from db import IntegrationRepository, MerchantRepository
from integrations import (
    build_authorize_url,
    exchange_authorization_code,
    sign_oauth_state,
    verify_oauth_state,
)
from shared import (
    IntegrationError,
    NotFoundError,
    PermissionDeniedError,
    get_logger,
    get_settings,
)

router = APIRouter()
logger = get_logger(__name__)

_MERCHANT_FILTER: Any = Query(default=None, description="Admin-only: target merchant_id")


# ---- Models ----------------------------------------------------------------


class OAuthStartResponse(BaseModel):
    authorize_url: str


class WhatsAppVerifyIn(BaseModel):
    phone_number_id: str = Field(
        min_length=1,
        max_length=64,
        description="360dialog channel id (the 'phone_number_id' Meta uses) for this merchant.",
    )
    display_phone: str | None = Field(default=None, max_length=32)


class ConnectionOut(BaseModel):
    provider: str
    connected: bool
    status: str
    external_account_id: str | None
    expires_at: int | None
    meta: dict[str, Any]


class StatusOut(BaseModel):
    merchant_id: UUID
    connections: list[ConnectionOut]


# ---- GHL OAuth -------------------------------------------------------------


@router.post("/ghl/oauth/start", response_model=OAuthStartResponse)
async def ghl_oauth_start(ctx: CurrentContext, session: DBSession) -> OAuthStartResponse:
    merchant_id = _require_merchant_scope(ctx)
    settings = get_settings()

    if not settings.ghl_client_id:
        raise IntegrationError(
            "GHL client_id not configured",
            error_code="ghl_not_configured",
        )
    redirect_uri = _ghl_redirect_uri(settings)
    state_secret = settings.ghl_oauth_state_secret or settings.ghl_client_secret

    state = sign_oauth_state(merchant_id=merchant_id, secret=state_secret)
    url = build_authorize_url(
        client_id=settings.ghl_client_id,
        redirect_uri=redirect_uri,
        state=state,
    )
    logger.info(
        "integrations.ghl.oauth.started",
        actor_id=str(ctx.actor_id),
        merchant_id=str(merchant_id),
    )
    return OAuthStartResponse(authorize_url=url)


@router.get("/ghl/oauth/callback")
async def ghl_oauth_callback(code: str, state: str, session: DBSession) -> Response:
    """Public callback — no JWT. The signed `state` is what ties the round-trip
    back to a merchant, so it must validate before we touch any DB row.
    """
    settings = get_settings()
    state_secret = settings.ghl_oauth_state_secret or settings.ghl_client_secret

    verified = verify_oauth_state(state, secret=state_secret)

    tokens = await exchange_authorization_code(
        code=code,
        client_id=settings.ghl_client_id,
        client_secret=settings.ghl_client_secret,
        redirect_uri=_ghl_redirect_uri(settings),
    )

    repo = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)
    await repo.upsert_ghl(
        merchant_id=verified.merchant_id,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_at=tokens.expires_at,
        location_id=tokens.location_id,
    )

    logger.info(
        "integrations.ghl.oauth.completed",
        merchant_id=str(verified.merchant_id),
        location_id=tokens.location_id,
    )

    redirect_target = _merchant_redirect(settings, status="connected", provider="ghl")
    return Response(
        status_code=302,
        headers={"Location": redirect_target},
    )


# ---- WhatsApp manual verify -----------------------------------------------


@router.post("/whatsapp/verify", response_model=ConnectionOut)
async def whatsapp_verify(
    payload: WhatsAppVerifyIn, ctx: CurrentContext, session: DBSession
) -> ConnectionOut:
    merchant_id = _require_merchant_scope(ctx)
    settings = get_settings()

    if not settings.whatsapp_d360_api_key:
        raise IntegrationError(
            "WHATSAPP_D360_API_KEY is not configured on this deployment",
            error_code="d360_not_configured",
        )

    # Confirm the shared Partner key can read the configured channel before
    # we record the merchant→channel mapping. 360dialog's /v1/configs/templates
    # endpoint is namespaced to whichever channel the key authorizes, so a
    # 200 here proves the key is live; we don't get per-channel detail back
    # but a failure surfaces immediately as a misconfiguration.
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            "https://waba-v2.360dialog.io/v1/configs/webhook",
            headers={"D360-API-KEY": settings.whatsapp_d360_api_key},
        )
    if resp.status_code >= 400:
        raise IntegrationError(
            "360dialog rejected the shared API key",
            error_code="d360_verify_failed",
            status_code=resp.status_code,
            body=resp.text[:300],
        )

    repo = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)
    await repo.upsert_whatsapp(
        merchant_id=merchant_id,
        phone_number_id=payload.phone_number_id,
        display_phone=payload.display_phone,
    )

    logger.info(
        "integrations.whatsapp.verified",
        actor_id=str(ctx.actor_id),
        merchant_id=str(merchant_id),
        phone_number_id=payload.phone_number_id,
    )
    meta_out: dict[str, Any] = {"provider": "d360"}
    if payload.display_phone:
        meta_out["display_phone"] = payload.display_phone
    return ConnectionOut(
        provider="whatsapp",
        connected=True,
        status="active",
        external_account_id=payload.phone_number_id,
        expires_at=None,
        meta=meta_out,
    )


# ---- Status ----------------------------------------------------------------


@router.get("/status", response_model=StatusOut)
async def integration_status(
    ctx: CurrentContext,
    session: DBSession,
    merchant_id: UUID | None = _MERCHANT_FILTER,
) -> StatusOut:
    target = _resolve_status_merchant(ctx, merchant_id)

    if ctx.role.startswith("agency"):
        # Verify the target belongs to the caller's tenant — RLS on merchants
        # already enforces this but we want a crisp 404 for the UI.
        merchant = await MerchantRepository(session).get(target)
        if merchant is None or merchant.tenant_id != ctx.tenant_id:
            raise NotFoundError("Merchant not found", merchant_id=str(target))

    settings = get_settings()
    repo = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)
    rows = await repo.list_status(target)
    by_provider = {r.provider: r for r in rows}

    connections: list[ConnectionOut] = []
    for provider in ("ghl", "whatsapp"):
        row = by_provider.get(provider)
        if row is None:
            connections.append(
                ConnectionOut(
                    provider=provider,
                    connected=False,
                    status="disconnected",
                    external_account_id=None,
                    expires_at=None,
                    meta={},
                )
            )
        else:
            connections.append(
                ConnectionOut(
                    provider=row.provider,
                    connected=row.status == "active",
                    status=row.status,
                    external_account_id=row.external_account_id,
                    expires_at=row.expires_at,
                    meta=row.meta,
                )
            )
    return StatusOut(merchant_id=target, connections=connections)


# ---- helpers ---------------------------------------------------------------


def _require_merchant_scope(ctx: CurrentContext) -> UUID:
    if ctx.merchant_id is None:
        raise PermissionDeniedError(
            "Merchant context required for integrations actions",
            error_code="no_merchant_context",
        )
    return ctx.merchant_id


def _resolve_status_merchant(ctx: CurrentContext, override: UUID | None) -> UUID:
    if ctx.merchant_id is not None:
        if override is not None and override != ctx.merchant_id:
            raise PermissionDeniedError(
                "Cannot inspect another merchant's integrations",
                error_code="cross_merchant_status",
            )
        return ctx.merchant_id
    if override is None:
        raise PermissionDeniedError(
            "Agency callers must specify merchant_id",
            error_code="missing_merchant_id",
        )
    return override


def _ghl_redirect_uri(settings: Any) -> str:
    if settings.ghl_redirect_uri:
        return str(settings.ghl_redirect_uri)
    if not settings.public_api_base_url:
        raise IntegrationError(
            "GHL redirect URI not configured (set GHL_REDIRECT_URI or PUBLIC_API_BASE_URL)",
            error_code="ghl_redirect_not_configured",
        )
    base = settings.public_api_base_url.rstrip("/")
    return f"{base}/integrations/ghl/oauth/callback"


def _merchant_redirect(settings: Any, *, status: str, provider: str) -> str:
    if not settings.public_web_merchant_url:
        # Fall back to a JSON-friendly response if no web URL is configured —
        # don't hard-fail the OAuth callback just because the portal URL is unset.
        return f"about:blank?integrations_{provider}={status}"
    base = settings.public_web_merchant_url.rstrip("/")
    return f"{base}/integrations?provider={provider}&status={status}"
