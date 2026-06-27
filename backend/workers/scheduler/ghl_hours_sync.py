"""Sincronizzazione notturna GHL → business_hours / business_closures.

Direzione: GHL → nostro DB (pull).

Il push nella direzione opposta avviene in modo sincrono al salvataggio degli
orari dal router /services/{merchant_id}/hours (PUT). Questo cron copre il caso
in cui il merchant modifichi gli orari direttamente dall'interfaccia GHL: entro
24h (al massimo) le modifiche si riflettono nel nostro DB.

Per ogni location GHL attiva con un calendar_id configurato:
  1. Legge il calendario GHL (GET /calendars/{id}).
  2. Converte openHours + dateOverrides nel nostro formato.
  3. Sovrascrive business_hours e aggiunge/rimuove business_closures.

La logica "last write wins" tra le due direzioni è garantita dalla sequenza:
  - Salvataggio UI → push a GHL → nightly cron ri-legge GHL → i dati tornano
    al DB invariati (nessun conflitto).
  - Modifica in GHL UI → nightly cron rileva il cambio → aggiorna il DB.
"""

from __future__ import annotations

import datetime
from typing import Any
from uuid import UUID

from config_resolver import ConfigKey, ConfigResolver
from db import GHLMarketplaceRepository, session_scope
from db.repositories.ghl_marketplace import ResolvedLocationToken
from db.repositories.services import BusinessClosureRepository, BusinessHourRepository
from integrations.ghl.calendar_sync import from_ghl_date_overrides, from_ghl_open_hours
from integrations.ghl.client import GHLClient, GHLTokenBundle
from shared import IntegrationError, get_logger, get_settings

logger = get_logger(__name__)


async def sync_ghl_calendar_hours(ctx: dict[str, Any]) -> dict[str, int]:
    """Pull orari di apertura da GHL per tutte le location collegate."""
    settings = get_settings()
    kek = settings.integrations_kek_base64

    merchants_ok = 0
    merchants_skip = 0
    merchants_fail = 0

    async with session_scope() as session:
        marketplace = GHLMarketplaceRepository(session, kek_base64=kek)
        config = ConfigResolver(session)
        locations = await marketplace.list_active_linked_locations()

        for loc in locations:
            if loc.merchant_id is None:
                continue
            calendar_id = await config.resolve(
                ConfigKey.BOOKING_DEFAULT_CALENDAR_ID, merchant_id=loc.merchant_id
            )
            if not calendar_id:
                merchants_skip += 1
                continue

            try:
                await _sync_one(
                    session=session,
                    loc=loc,
                    calendar_id=str(calendar_id),
                    kek=kek,
                    client_id=settings.ghl_client_id,
                    client_secret=settings.ghl_client_secret,
                )
                merchants_ok += 1
            except Exception as exc:
                logger.warning(
                    "ghl_hours_sync.merchant_failed",
                    merchant_id=str(loc.merchant_id),
                    error=str(exc),
                )
                merchants_fail += 1

    logger.info(
        "ghl_hours_sync.done",
        ok=merchants_ok,
        skipped=merchants_skip,
        failed=merchants_fail,
    )
    return {"ok": merchants_ok, "skipped": merchants_skip, "failed": merchants_fail}


async def _sync_one(
    *,
    session: Any,
    loc: ResolvedLocationToken,
    calendar_id: str,
    kek: str,
    client_id: str,
    client_secret: str,
) -> None:
    merchant_id: UUID = loc.merchant_id  # type: ignore[assignment]

    async def _persist(bundle: GHLTokenBundle) -> None:
        if not bundle.location_id:
            return
        try:
            async with session_scope() as s:
                await GHLMarketplaceRepository(s, kek_base64=kek).set_location_token(
                    location_id=bundle.location_id,
                    access_token=bundle.access_token,
                    refresh_token=bundle.refresh_token,
                    expires_at=bundle.expires_at,
                )
        except Exception as exc:
            logger.error("ghl_hours_sync.token_persist_failed", error=str(exc))

    client = GHLClient(
        token_bundle=GHLTokenBundle(
            access_token=loc.access_token,
            refresh_token=loc.refresh_token,
            expires_at=loc.expires_at,
            location_id=loc.location_id,
        ),
        client_id=client_id,
        client_secret=client_secret,
        on_token_refresh=_persist,
    )

    try:
        calendar = await client.get_calendar(calendar_id)
    finally:
        await client.close()

    ghl_open_hours: list[dict] = calendar.get("openHours") or []
    ghl_date_overrides: list[dict] = calendar.get("dateOverrides") or []

    hour_dicts = from_ghl_open_hours(ghl_open_hours)
    closure_dicts = from_ghl_date_overrides(ghl_date_overrides)

    hours_repo = BusinessHourRepository(session)
    await hours_repo.upsert_bulk(merchant_id, hour_dicts)

    # Per le chiusure: rimuovi quelle future non più presenti in GHL, poi aggiungi le nuove.
    closure_repo = BusinessClosureRepository(session)
    today = datetime.date.today()
    existing = await closure_repo.list(merchant_id, from_date=today)
    existing_dates = {c.closed_on for c in existing}
    incoming_dates = {d["closed_on"] for d in closure_dicts}

    # Rimuovi chiusure che non esistono più in GHL.
    for c in existing:
        if c.closed_on not in incoming_dates:
            await closure_repo.delete(merchant_id, c.id)

    # Aggiungi quelle nuove.
    for d in closure_dicts:
        if d["closed_on"] not in existing_dates:
            await closure_repo.add(
                merchant_id=merchant_id,
                closed_on=d["closed_on"],
                label=d.get("label"),
            )

    logger.info(
        "ghl_hours_sync.merchant_ok",
        merchant_id=str(merchant_id),
        calendar_id=calendar_id,
        hours_rows=len(hour_dicts),
        closures_rows=len(closure_dicts),
    )
