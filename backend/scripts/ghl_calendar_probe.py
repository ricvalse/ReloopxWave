"""Probe read-only sui calendari GHL di un merchant: lista i calendari e gli
slot liberi in una finestra, per verificare se gli eventi di un Google Calendar
collegato lato GHL spariscono davvero da `/calendars/{id}/free-slots`.

Run da backend/:

    export SUPABASE_DB_URL='postgresql+asyncpg://postgres.<ref>:<pwd>@aws-0-eu-west-1.pooler.supabase.com:5432/postgres'
    export INTEGRATIONS_KEK_BASE64='<da Railway>'
    export GHL_CLIENT_ID='...' GHL_CLIENT_SECRET='...'
    uv run python scripts/ghl_calendar_probe.py <merchant_uuid> 2026-07-16 2026-07-19
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from db import GHLMarketplaceRepository, IntegrationRepository, get_engine, session_scope
from integrations.ghl.client import GHLClient, GHLTokenBundle
from shared import get_settings

TZ = ZoneInfo("Europe/Rome")


def _day_bounds(day: str) -> str:
    # ISO offset-aware: _iso_to_epoch_ms tratta gli ISO naive come UTC, e con
    # l'ora legale italiana sarebbero 2h di scarto sulla finestra.
    return datetime.fromisoformat(day).replace(tzinfo=TZ).isoformat()


async def main(merchant_id: UUID, start_day: str, end_day: str) -> None:
    settings = get_settings()
    kek = settings.integrations_kek_base64
    if not kek:
        sys.exit("INTEGRATIONS_KEK_BASE64 non configurata: impossibile decifrare il token.")

    # Obbligatorio prima di qualsiasi session_scope(): inizializza la session factory.
    get_engine(settings.supabase_db_url)

    async with session_scope() as session:
        ghl = await IntegrationRepository(session, kek_base64=kek).resolve_ghl(merchant_id)

    if ghl is None:
        # resolve_ghl filtra status == 'active': pending_link/error/revoked -> None, in silenzio.
        async with session_scope() as session:
            summary = await GHLMarketplaceRepository(
                session, kek_base64=kek
            ).resolve_location_summary_by_merchant(merchant_id)
        sys.exit(f"Nessun location token attivo per {merchant_id}. Summary: {summary}")

    print(f"location_id={ghl.location_id}  tenant={ghl.tenant_id}  expires_at={ghl.expires_at}")

    async def _persist(bundle: GHLTokenBundle) -> None:
        # GHL invalida il vecchio refresh_token a ogni rotazione: senza questa
        # persistenza (transazione propria, committata) lo script romperebbe
        # l'integrazione in produzione in modo permanente.
        if not bundle.location_id:
            return
        try:
            async with session_scope() as token_session:
                await GHLMarketplaceRepository(token_session, kek_base64=kek).set_location_token(
                    location_id=bundle.location_id,
                    access_token=bundle.access_token,
                    refresh_token=bundle.refresh_token,
                    expires_at=bundle.expires_at,
                )
            print("!! refresh_token ruotato e persistito")
        except Exception as exc:  # on_token_refresh e' best-effort: il client inghiotte l'errore
            print(
                f"!!! PERSISTENZA FALLITA — INTEGRAZIONE A RISCHIO: {exc}\n"
                f"!!! Il DB ora ha un refresh_token morto: serve re-auth dell'agenzia.",
                file=sys.stderr,
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
        calendars = await client.list_calendars(ghl.location_id or "")
        if not calendars:
            print("Nessun calendario sulla location.")
            return
        start_iso, end_iso = _day_bounds(start_day), _day_bounds(end_day)
        for cal in calendars:
            print(f"\n=== {cal.get('name')}  [{cal.get('id')}]")
            slots = await client.get_free_slots(cal["id"], start_iso=start_iso, end_iso=end_iso)
            if not slots:
                print("   (nessuno slot libero nella finestra)")
            for slot in slots:
                print(f"   {slot['startTime']}")
    finally:
        await client.close()


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit("usage: ghl_calendar_probe.py <merchant_uuid> <YYYY-MM-DD> <YYYY-MM-DD>")
    asyncio.run(main(UUID(sys.argv[1]), sys.argv[2], sys.argv[3]))
