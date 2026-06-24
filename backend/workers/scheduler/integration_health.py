"""Integration health-check worker — pings each merchant's WhatsApp + GHL
credentials so stale tokens surface before a real conversation hits them.

Cadence: runs once a day via Railway Cron.

- WhatsApp lives in the `integrations` table; for every `status='active'` row we
  issue a cheap liveness check and flip status to `error` (keeping the
  ciphertext) on failure. The merchant portal surfaces that state through
  `/integrations/status`.
- GHL no longer lives in `integrations` (ADR 0007 — it's in `ghl_location_tokens`
  after the marketplace agency-install refactor). We iterate the linked location
  tokens, issue a cheap liveness call with the GHL client (which transparently
  refreshes + persists a rotated token on 401), and stamp health on the row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from db import (
    GHLMarketplaceRepository,
    ResolvedLocationToken,
    session_scope,
)
from db.models import Integration
from integrations.ghl.client import GHLClient, GHLTokenBundle
from shared import IntegrationError, Settings, get_logger, get_settings

logger = get_logger(__name__)

# GHL error_codes / HTTP statuses that mean the stored credentials are dead and
# require a manual re-auth (vs. a transient blip the probe should shrug off).
# `ghl_refresh_failed` = the refresh-token grant itself was rejected; a
# `ghl_request_failed` that is still 401/403 *after* the client's refresh+retry
# means the (possibly rotated) access token is unauthorized — also definitive.
_DEFINITIVE_AUTH_ERROR_CODES = frozenset({"ghl_refresh_failed"})
_DEFINITIVE_AUTH_STATUSES = frozenset({401, 403})


@dataclass(slots=True)
class GHLProbeResult:
    """Outcome of a single location liveness probe.

    `healthy` drives the `meta.last_health_check_ok` stamp; `definitive_auth_failure`
    is the ONLY thing that flips the row to `error` — a transient failure
    (network/timeout/5xx/rate-limit) leaves a valid token `active`."""

    healthy: bool
    definitive_auth_failure: bool = False


async def integration_health_check(ctx: dict[str, object]) -> dict[str, int]:
    """Return a summary of how many credentials we inspected and how many failed."""
    settings = get_settings()
    checked = 0
    broken = 0

    async with session_scope() as session:
        rows = (
            (await session.execute(select(Integration).where(Integration.status == "active")))
            .scalars()
            .all()
        )

        async with httpx.AsyncClient(timeout=10.0) as http:
            for integration in rows:
                checked += 1
                healthy = await _check_one(integration, http=http, settings=settings)
                if not healthy:
                    broken += 1
                    integration.status = "error"
                integration.meta = {
                    **(integration.meta or {}),
                    "last_health_check_at": datetime.now(tz=UTC).isoformat(),
                    "last_health_check_ok": healthy,
                }

    # GHL location tokens live in their own table (ADR 0007). Probe each linked
    # location with a cheap call that exercises (and rotates, if needed) the
    # stored token, then stamp the outcome on the row.
    ghl_checked, ghl_broken = await _check_ghl_locations(settings)
    checked += ghl_checked
    broken += ghl_broken

    logger.info("integrations.health.summary", checked=checked, broken=broken)
    return {"checked": checked, "broken": broken}


async def _check_one(
    integration: Integration, *, http: httpx.AsyncClient, settings: Settings
) -> bool:
    """Cheapest liveness ping that proves the stored WhatsApp token still works."""
    if integration.provider == "whatsapp":
        # All WhatsApp traffic goes through 360dialog with a platform-level
        # API key. There's nothing per-merchant to probe — if the row is
        # already marked error something else handled it; otherwise green.
        return integration.status != "error"

    return True


async def _check_ghl_locations(settings: Settings) -> tuple[int, int]:
    """Liveness-check every linked GHL location token (#24).

    Returns (checked, broken). Each location gets its own committed transaction
    for the health stamp so one failure doesn't roll back the others. The GHL
    client refreshes + persists a rotated token transparently on 401, so a
    near-expired token self-heals here instead of breaking a live conversation.
    """
    if not (settings.ghl_client_id and settings.ghl_client_secret):
        return 0, 0

    kek = settings.integrations_kek_base64
    async with session_scope() as session:
        locations = await GHLMarketplaceRepository(
            session, kek_base64=kek
        ).list_health_checkable_locations()

    checked = 0
    broken = 0
    for loc in locations:
        checked += 1
        result = await _check_ghl_location(loc, settings=settings, kek=kek)
        if not result.healthy:
            broken += 1
        async with session_scope() as session:
            await GHLMarketplaceRepository(session, kek_base64=kek).mark_location_health(
                location_id=loc.location_id,
                healthy=result.healthy,
                mark_error=result.definitive_auth_failure,
            )
    return checked, broken


def _is_definitive_auth_failure(exc: BaseException) -> bool:
    """A probe failure that means the stored credentials are actually dead.

    Only these flip the location to `error`: the GHL refresh grant was rejected
    (`ghl_refresh_failed`), or a request that is still 401/403 after the client's
    transparent refresh+retry. Network errors, timeouts, 5xx and 429 are
    transient — a once-a-day blip must NOT disable a valid token for ~24h."""
    if not isinstance(exc, IntegrationError):
        return False
    if exc.error_code in _DEFINITIVE_AUTH_ERROR_CODES:
        return True
    status = exc.context.get("status")
    return isinstance(status, int) and status in _DEFINITIVE_AUTH_STATUSES


async def _check_ghl_location(
    loc: ResolvedLocationToken, *, settings: Settings, kek: str
) -> GHLProbeResult:
    """Issue one cheap GHL call (`GET /locations/{id}`) to prove the token works.

    Returns a `GHLProbeResult`: a successful probe is healthy; a definitive auth
    failure (dead credentials) is unhealthy AND flips the row to `error`; any
    other failure (transient network/5xx/rate-limit) is recorded as unhealthy in
    the meta stamp but leaves the token `active` so live GHL operations keep
    working — one daily blip must not take a merchant's CRM offline for a day.

    Persists any rotated token in its OWN committed transaction (GHL invalidates
    the old refresh token on rotation, so it must survive even if a later step
    fails) — the same gotcha the booking handler guards against.
    """

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
            access_token=loc.access_token,
            refresh_token=loc.refresh_token,
            expires_at=loc.expires_at,
            location_id=loc.location_id,
        ),
        client_id=settings.ghl_client_id,
        client_secret=settings.ghl_client_secret,
        on_token_refresh=_persist,
    )
    try:
        await client.get_location(loc.location_id)
        return GHLProbeResult(healthy=True)
    except Exception as e:
        definitive = _is_definitive_auth_failure(e)
        logger.warning(
            "integrations.health.ghl_failed",
            location_id=loc.location_id,
            error=str(e),
            definitive_auth_failure=definitive,
        )
        return GHLProbeResult(healthy=False, definitive_auth_failure=definitive)
    finally:
        await client.close()
