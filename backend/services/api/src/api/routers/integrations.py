"""Per-merchant external integrations — GHL (OAuth2) + WhatsApp (router-mediated).

Scope:
- `POST /integrations/ghl/oauth/start` — mints a signed state, returns the
  GHL marketplace URL. Merchant user opens it in a new tab.
- `GET /integrations/ghl/oauth/callback` — verifies state, exchanges code,
  encrypts + persists the token bundle, redirects to the merchant portal.
- `POST /integrations/whatsapp/onboard/start` — server-to-server proxy to
  the router's `/onboard/start`. Mints a one-shot state token tied to the
  caller's merchant_id and returns the assembled 360dialog Embedded Signup
  URL the browser should navigate to. The router's `/onboard/callback` is
  what 360dialog redirects back to; we receive the resulting channel via
  `POST /internal/whatsapp-connected` (see `internal.py`).
- `GET /integrations/status` — returns connection cards for the caller's
  merchant (agency admins can pass `?merchant_id=<uuid>` to view a specific
  merchant under their tenant).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from api.dependencies.session import CurrentContext, DBSession
from db import IntegrationRepository, MerchantRepository
from integrations import (
    RouterClient,
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


class WhatsAppOnboardStartIn(BaseModel):
    """Optional override for where the browser lands after the router's
    `/onboard/callback` finishes. Defaults to the merchant portal's
    `/integrations?provider=whatsapp&status=connected`.
    """

    return_url: str | None = Field(default=None, max_length=512)


class WhatsAppOnboardStartOut(BaseModel):
    # Full URL the frontend should navigate to. The router builds it
    # server-side using its own copy of the 360dialog Partner ID, so the
    # browser (and this service) never sees the partner_id.
    signup_url: str
    state: str
    expires_in: int


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


# ---- WhatsApp router-mediated onboarding ----------------------------------


@router.post("/whatsapp/onboard/start", response_model=WhatsAppOnboardStartOut)
async def whatsapp_onboard_start(
    payload: WhatsAppOnboardStartIn,
    ctx: CurrentContext,
    session: DBSession,
) -> WhatsAppOnboardStartOut:
    """Mint a router state token and return the 360dialog signup URL.

    The router assembles the full Embedded Signup URL (`connect_url`) using
    its own copy of the partner_id and returns it; we just hand it to the
    browser. 360dialog then redirects to
    `<router>/onboard/callback?platform=...&state=...`, the router fetches
    the per-channel D360 key, fires `POST /internal/whatsapp-connected` to
    us, and finally redirects the browser back to `return_url`.
    """
    merchant_id = _require_merchant_scope(ctx)
    settings = get_settings()

    _require_router_config(settings)

    return_url = payload.return_url or _default_return_url(settings)

    client = RouterClient(
        base_url=settings.router_base_url,
        shared_secret=settings.router_shared_secret,
    )
    try:
        result = await client.onboard_start(
            platform_id=settings.router_platform_id,
            customer_id=merchant_id,
            return_url=return_url,
        )
    finally:
        await client.close()

    logger.info(
        "integrations.whatsapp.onboard.started",
        actor_id=str(ctx.actor_id),
        merchant_id=str(merchant_id),
        expires_in=result.expires_in,
    )
    return WhatsAppOnboardStartOut(
        signup_url=result.connect_url,
        state=result.state,
        expires_in=result.expires_in,
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


def _require_router_config(settings: Any) -> None:
    missing = [
        name
        for name, value in (
            ("ROUTER_BASE_URL", settings.router_base_url),
            ("ROUTER_SHARED_SECRET", settings.router_shared_secret),
            ("ROUTER_PLATFORM_ID", settings.router_platform_id),
        )
        if not value
    ]
    if missing:
        raise IntegrationError(
            f"Router config missing: {', '.join(missing)}",
            error_code="router_not_configured",
        )


def _default_return_url(settings: Any) -> str:
    if settings.public_web_merchant_url:
        base = settings.public_web_merchant_url.rstrip("/")
        return f"{base}/integrations?provider=whatsapp&status=connected"
    # Last-resort fallback so /onboard/start can mint a state even on a
    # freshly-deployed environment without the merchant URL yet — the
    # operator should still set PUBLIC_WEB_MERCHANT_URL before going live.
    return "about:blank?integrations_whatsapp=connected"


def _merchant_redirect(settings: Any, *, status: str, provider: str) -> str:
    if not settings.public_web_merchant_url:
        # Fall back to a JSON-friendly response if no web URL is configured —
        # don't hard-fail the OAuth callback just because the portal URL is unset.
        return f"about:blank?integrations_{provider}={status}"
    base = settings.public_web_merchant_url.rstrip("/")
    return f"{base}/integrations?provider={provider}&status={status}"
