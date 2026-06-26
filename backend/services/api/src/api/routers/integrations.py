"""External integrations — GHL marketplace (agency OAuth2) + WhatsApp (router-mediated).

GHL is wired as the marketplace agency-install model (ADR 0007): the *agency*
(= tenant) connects once, then locations arrive via INSTALL webhooks and are
linked to merchants. There is no per-merchant GHL self-service flow.

Scope:
- `POST /integrations/ghl/agency/oauth/start` — agency admin only. Mints a
  signed state (tenant_id), returns the GHL marketplace URL.
- `GET /integrations/crm/oauth/callback` — verifies state, exchanges the code
  for an Agency (Company) token, persists `ghl_agency_installs`, redirects to
  the admin portal. NOTE: the path deliberately avoids the substring "ghl" —
  GoHighLevel rejects any OAuth redirect URI that contains a HighLevel brand
  reference (highlevel / gohighlevel / ghl). The `start` path keeps "ghl" since
  GHL never validates it.
- `GET /integrations/ghl/agency/status` — agency token connection status.
- `GET /integrations/ghl/locations` + `POST .../{location_id}/link` — list the
  installed locations and link each to an existing merchant.
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
from db import (
    GHLMarketplaceRepository,
    GhlSyncRepository,
    IntegrationRepository,
    MerchantRepository,
    session_scope,
)
from integrations import (
    RouterClient,
    build_authorize_url,
    exchange_authorization_code,
    sign_oauth_state,
    verify_oauth_state,
)
from integrations.ghl.client import GHLClient, GHLTokenBundle
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


class AgencyStatusOut(BaseModel):
    connected: bool
    company_id: str | None
    company_name: str | None
    expires_at: int | None


class LocationOut(BaseModel):
    location_id: str
    location_name: str | None
    status: str
    merchant_id: UUID | None
    company_id: str


class LocationsOut(BaseModel):
    locations: list[LocationOut]


class LinkLocationIn(BaseModel):
    merchant_id: UUID


class CalendarOut(BaseModel):
    id: str
    name: str | None = None


class CalendarsOut(BaseModel):
    calendars: list[CalendarOut]


# ---- GHL marketplace (agency OAuth + location linking) ---------------------


@router.post("/ghl/agency/oauth/start", response_model=OAuthStartResponse)
async def ghl_agency_oauth_start(ctx: CurrentContext, session: DBSession) -> OAuthStartResponse:
    tenant_id = _require_agency_scope(ctx)
    settings = get_settings()

    if not settings.ghl_client_id:
        raise IntegrationError(
            "GHL client_id not configured",
            error_code="ghl_not_configured",
        )
    redirect_uri = _ghl_redirect_uri(settings)
    state_secret = settings.ghl_oauth_state_secret or settings.ghl_client_secret

    state = sign_oauth_state(tenant_id=tenant_id, secret=state_secret)
    url = build_authorize_url(
        client_id=settings.ghl_client_id,
        redirect_uri=redirect_uri,
        state=state,
    )
    logger.info(
        "integrations.ghl.agency.oauth.started",
        actor_id=str(ctx.actor_id),
        tenant_id=str(tenant_id),
    )
    return OAuthStartResponse(authorize_url=url)


@router.get("/crm/oauth/callback")
async def ghl_oauth_callback(code: str, state: str) -> Response:
    """Public callback — no JWT. This is a browser redirect from GHL, so it
    carries no Supabase Authorization header; the signed `state` is what ties
    the round-trip back to a tenant. We therefore must NOT use the tenant-scoped
    `DBSession` dependency (it runs JWT verification and would 403). The state
    validates first, then we exchange the code for an Agency (Company) token and
    persist it through an unscoped service-role session.
    """
    settings = get_settings()
    state_secret = settings.ghl_oauth_state_secret or settings.ghl_client_secret

    verified = verify_oauth_state(state, secret=state_secret)

    tokens = await exchange_authorization_code(
        code=code,
        client_id=settings.ghl_client_id,
        client_secret=settings.ghl_client_secret,
        redirect_uri=_ghl_redirect_uri(settings),
        user_type="Company",
    )
    if not tokens.company_id:
        raise IntegrationError(
            "GHL agency token response missing companyId",
            error_code="ghl_company_missing",
        )

    company_name = tokens.raw.get("companyName") or tokens.raw.get("company_name")
    async with session_scope() as session:
        repo = GHLMarketplaceRepository(session, kek_base64=settings.integrations_kek_base64)
        await repo.upsert_agency_install(
            tenant_id=verified.tenant_id,
            company_id=tokens.company_id,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            company_name=str(company_name) if company_name else None,
        )

    logger.info(
        "integrations.ghl.agency.oauth.completed",
        tenant_id=str(verified.tenant_id),
        company_id=tokens.company_id,
    )

    redirect_target = _admin_redirect(settings, status="connected", provider="ghl_agency")
    return Response(status_code=302, headers={"Location": redirect_target})


@router.get("/ghl/agency/status", response_model=AgencyStatusOut)
async def ghl_agency_status(ctx: CurrentContext, session: DBSession) -> AgencyStatusOut:
    tenant_id = _require_agency_scope(ctx)
    settings = get_settings()
    repo = GHLMarketplaceRepository(session, kek_base64=settings.integrations_kek_base64)
    install = await repo.resolve_agency_by_tenant(tenant_id)
    if install is None:
        return AgencyStatusOut(connected=False, company_id=None, company_name=None, expires_at=None)
    return AgencyStatusOut(
        connected=True,
        company_id=install.company_id,
        company_name=install.company_name,
        expires_at=install.expires_at,
    )


@router.get("/ghl/locations", response_model=LocationsOut)
async def ghl_locations(ctx: CurrentContext, session: DBSession) -> LocationsOut:
    tenant_id = _require_agency_scope(ctx)
    settings = get_settings()
    repo = GHLMarketplaceRepository(session, kek_base64=settings.integrations_kek_base64)
    rows = await repo.list_locations(tenant_id)
    return LocationsOut(
        locations=[
            LocationOut(
                location_id=r.location_id,
                location_name=r.location_name,
                status=r.status,
                merchant_id=r.merchant_id,
                company_id=r.company_id,
            )
            for r in rows
        ]
    )


@router.post("/ghl/locations/{location_id}/link", status_code=204)
async def ghl_link_location(
    location_id: str,
    payload: LinkLocationIn,
    ctx: CurrentContext,
    session: DBSession,
) -> Response:
    tenant_id = _require_agency_scope(ctx)
    settings = get_settings()
    # The target merchant must belong to the caller's tenant (RLS enforces this
    # too; we want a crisp 404 for the UI).
    merchant = await MerchantRepository(session).get(payload.merchant_id)
    if merchant is None or merchant.tenant_id != tenant_id:
        raise NotFoundError("Merchant not found", merchant_id=str(payload.merchant_id))
    repo = GHLMarketplaceRepository(session, kek_base64=settings.integrations_kek_base64)
    linked = await repo.link_location(location_id=location_id, merchant_id=payload.merchant_id)
    if not linked:
        raise NotFoundError("GHL location not found", location_id=location_id)
    logger.info(
        "integrations.ghl.location.linked",
        tenant_id=str(tenant_id),
        location_id=location_id,
        merchant_id=str(payload.merchant_id),
    )
    return Response(status_code=204)


@router.post("/ghl/locations/{location_id}/unlink", status_code=204)
async def ghl_unlink_location(
    location_id: str,
    ctx: CurrentContext,
    session: DBSession,
) -> Response:
    _require_agency_scope(ctx)
    settings = get_settings()
    repo = GHLMarketplaceRepository(session, kek_base64=settings.integrations_kek_base64)
    unlinked = await repo.unlink_location(location_id=location_id)
    if not unlinked:
        raise NotFoundError("GHL location not found", location_id=location_id)
    return Response(status_code=204)


@router.get("/ghl/calendars", response_model=CalendarsOut)
async def ghl_calendars(
    ctx: CurrentContext,
    session: DBSession,
    merchant_id: UUID | None = _MERCHANT_FILTER,
) -> CalendarsOut:
    """Calendars available for the merchant's linked GHL location (UC-02 picker).

    Returns an empty list (not an error) when GHL isn't connected yet, so the
    booking-config UI degrades to the manual calendar-id field.
    """
    target = merchant_id or _require_merchant_scope(ctx)
    settings = get_settings()
    kek = settings.integrations_kek_base64
    ghl = await IntegrationRepository(session, kek_base64=kek).resolve_ghl(target)
    if ghl is None or not ghl.location_id:
        return CalendarsOut(calendars=[])

    async def _persist(bundle: GHLTokenBundle) -> None:
        if not bundle.location_id:
            return
        async with session_scope() as token_session:
            await GHLMarketplaceRepository(token_session, kek_base64=kek).set_location_token(
                location_id=bundle.location_id,
                access_token=bundle.access_token,
                refresh_token=bundle.refresh_token,
                expires_at=bundle.expires_at,
            )

    client = GHLClient(
        token_bundle=GHLTokenBundle(
            access_token=ghl.access_token,
            refresh_token=ghl.refresh_token,
            expires_at=ghl.expires_at,
            location_id=ghl.location_id,
        ),
        client_id=settings.ghl_client_id,
        client_secret=settings.ghl_client_secret,
        on_token_refresh=_persist,
    )
    try:
        raw = await client.list_calendars(ghl.location_id)
    except IntegrationError as exc:
        logger.warning("ghl.calendars.failed", merchant_id=str(target), error_code=exc.error_code)
        return CalendarsOut(calendars=[])
    finally:
        await client.close()

    calendars = [
        CalendarOut(id=str(c["id"]), name=c.get("name"))
        for c in raw
        if isinstance(c, dict) and c.get("id")
    ]
    return CalendarsOut(calendars=calendars)


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


@router.post("/whatsapp/disconnect", status_code=204)
async def whatsapp_disconnect(ctx: CurrentContext, session: DBSession) -> Response:
    """Wipe the calling merchant's WhatsApp integration row.

    Used by the merchant portal's "Sostituisci canale" button: clears local
    state so the merchant immediately sees a "no channel" UI, then the same
    flow re-runs Embedded Signup. If they complete signup the router's
    `/internal/whatsapp-connected` notify re-creates the row; if they cancel
    they're left disconnected. The router's `waba_mapping` for the old
    `phone_number_id` is NOT cleaned up here — that needs a merchant-scoped
    router endpoint we don't have yet.
    """
    merchant_id = _require_merchant_scope(ctx)
    settings = get_settings()
    repo = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)
    removed = await repo.disconnect_whatsapp(merchant_id)
    logger.info(
        "integrations.whatsapp.disconnected",
        actor_id=str(ctx.actor_id),
        merchant_id=str(merchant_id),
        had_row=removed,
    )
    return Response(status_code=204)


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
    by_provider = {r.provider: r for r in await repo.list_status(target)}

    connections: list[ConnectionOut] = []

    # GHL is the marketplace agency-install model: the link lives in
    # ghl_location_tokens (agency-managed), not in integrations. Surface it as a
    # read-only card so the merchant sees "managed by agency" rather than a
    # self-service connect button.
    ghl_repo = GHLMarketplaceRepository(session, kek_base64=settings.integrations_kek_base64)
    ghl_loc = await ghl_repo.resolve_location_summary_by_merchant(target)
    if ghl_loc is None:
        connections.append(
            ConnectionOut(
                provider="ghl",
                connected=False,
                status="disconnected",
                external_account_id=None,
                expires_at=None,
                meta={"created_via": "agency_install"},
            )
        )
    else:
        meta: dict[str, Any] = {"created_via": "agency_install"}
        if ghl_loc.location_name:
            meta["location_name"] = ghl_loc.location_name
        connections.append(
            ConnectionOut(
                provider="ghl",
                connected=ghl_loc.status == "active",
                status=ghl_loc.status,
                external_account_id=ghl_loc.location_id,
                expires_at=ghl_loc.expires_at,
                meta=meta,
            )
        )

    # WhatsApp stays in the integrations table (merchant self-service onboarding).
    wa = by_provider.get("whatsapp")
    if wa is None:
        connections.append(
            ConnectionOut(
                provider="whatsapp",
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
                provider=wa.provider,
                connected=wa.status == "active",
                status=wa.status,
                external_account_id=wa.external_account_id,
                expires_at=wa.expires_at,
                meta=wa.meta,
            )
        )

    return StatusOut(merchant_id=target, connections=connections)


# ---- GHL sync log ----------------------------------------------------------


class GhlSyncEntryOut(BaseModel):
    id: UUID
    lead_id: UUID | None
    conversation_id: UUID | None
    operation: str
    ghl_entity_type: str | None
    ghl_entity_id: str | None
    status: str
    error_detail: str | None
    payload: dict[str, Any] | None
    result: dict[str, Any] | None
    occurred_at: Any


class GhlSyncLogOut(BaseModel):
    entries: list[GhlSyncEntryOut]


@router.get("/ghl/sync-log", response_model=GhlSyncLogOut)
async def ghl_sync_log(
    ctx: CurrentContext,
    session: DBSession,
    merchant_id: UUID | None = _MERCHANT_FILTER,
    lead_id: UUID | None = Query(default=None, description="Filter by lead"),  # noqa: B008
    since_days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=500),
) -> GhlSyncLogOut:
    """Storico di ogni operazione GHL — per merchant o per singolo lead."""
    target = _resolve_status_merchant(ctx, merchant_id)
    repo = GhlSyncRepository(session)
    if lead_id is not None:
        entries = await repo.list_for_lead(lead_id, limit=limit)
    else:
        entries = await repo.list_for_merchant(target, since_days=since_days, limit=limit)
    return GhlSyncLogOut(
        entries=[
            GhlSyncEntryOut(
                id=e.id,
                lead_id=e.lead_id,
                conversation_id=e.conversation_id,
                operation=e.operation,
                ghl_entity_type=e.ghl_entity_type,
                ghl_entity_id=e.ghl_entity_id,
                status=e.status,
                error_detail=e.error_detail,
                payload=e.payload,
                result=e.result,
                occurred_at=e.occurred_at,
            )
            for e in entries
        ]
    )


# ---- helpers ---------------------------------------------------------------


def _require_merchant_scope(ctx: CurrentContext) -> UUID:
    if ctx.merchant_id is None:
        raise PermissionDeniedError(
            "Merchant context required for integrations actions",
            error_code="no_merchant_context",
        )
    return ctx.merchant_id


def _require_agency_scope(ctx: CurrentContext) -> UUID:
    """GHL marketplace actions are agency-level — the tenant connects, not a
    single merchant. Require an agency role and return the tenant_id."""
    if not ctx.role.startswith("agency"):
        raise PermissionDeniedError(
            "Agency context required for GHL marketplace actions",
            error_code="not_agency_scope",
        )
    return ctx.tenant_id


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
    # Path must NOT contain "ghl"/"highlevel" — GHL rejects redirect URIs with a
    # HighLevel brand reference. Keep this in sync with the route above.
    return f"{base}/integrations/crm/oauth/callback"


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


def _admin_redirect(settings: Any, *, status: str, provider: str) -> str:
    if not settings.public_web_admin_url:
        # Fall back to a JSON-friendly response if no web URL is configured —
        # don't hard-fail the OAuth callback just because the portal URL is unset.
        return f"about:blank?integrations_{provider}={status}"
    base = settings.public_web_admin_url.rstrip("/")
    return f"{base}/integrations?provider={provider}&status={status}"
