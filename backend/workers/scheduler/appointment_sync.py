"""Appointment reconcile poll (UC-02).

GHL sends no appointment webhooks, so write-through alone only mirrors the
bot's own bookings — a reschedule/cancel/new appointment made directly inside
GHL would never reach us. This job closes that gap: every linked merchant's
calendar is polled over a rolling window and each event is upserted into the
local `appointments` mirror, keyed idempotently on (merchant_id,
ghl_appointment_id). That makes the mirror a faithful COPY of GHL (the user's
ask), eventually-consistent within the poll interval.

Runs without a JWT under the service-role `session_scope()`, scoped per
merchant — modeled on `integration_health_check`. The cadence and window are
deliberately modest to stay well inside GHL's rate limits.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from config_resolver import ConfigKey, ConfigResolver
from db import (
    AppointmentRepository,
    GHLMarketplaceRepository,
    LeadRepository,
    build_reminder_schedule,
    next_reminder_due,
    session_scope,
)
from db.repositories.ghl_marketplace import ResolvedLocationToken
from integrations.ghl.client import GHLClient, GHLTokenBundle
from shared import IntegrationError, get_logger, get_settings

logger = get_logger(__name__)

# Rolling window polled each tick. Past appointments are immutable history we
# already captured via write-through; the value is in the upcoming agenda.
SYNC_HORIZON_DAYS = 30

# GHL appointmentStatus values that mean "the appointment stands". Anything else
# (cancelled, noshow, invalid) is stored verbatim so the agenda can reflect it.
_BOOKED_STATUSES = {"new", "confirmed", "booked", "showed", "scheduled"}


async def sync_appointments(ctx: dict[str, object]) -> dict[str, int]:
    """Poll GHL calendars for all linked merchants and upsert the local mirror."""
    settings = get_settings()
    kek = settings.integrations_kek_base64
    merchants = 0
    upserted = 0
    failed = 0

    window_start = datetime.now(tz=UTC)
    window_end = window_start + timedelta(days=SYNC_HORIZON_DAYS)

    async with session_scope() as session:
        marketplace = GHLMarketplaceRepository(session, kek_base64=kek)
        config = ConfigResolver(session)
        leads = LeadRepository(session)
        appts = AppointmentRepository(session)
        locations = await marketplace.list_active_linked_locations()

        for loc in locations:
            if loc.merchant_id is None:
                continue
            calendar_id = await config.resolve(
                ConfigKey.BOOKING_DEFAULT_CALENDAR_ID, merchant_id=loc.merchant_id
            )
            if not calendar_id:
                continue

            # Risolvi la configurazione multi-reminder del merchant (lista di ore).
            reminder_lead_hours_raw = await config.resolve(
                ConfigKey.BOOKING_REMINDER_SCHEDULE, merchant_id=loc.merchant_id
            )
            reminder_lead_hours: list[int] = (
                list(reminder_lead_hours_raw) if reminder_lead_hours_raw else [24]
            )

            try:
                events = await _fetch_events(
                    loc=loc,
                    calendar_id=str(calendar_id),
                    start_iso=window_start.isoformat(),
                    end_iso=window_end.isoformat(),
                    client_id=settings.ghl_client_id,
                    client_secret=settings.ghl_client_secret,
                    kek=kek,
                )
            except IntegrationError as e:
                failed += 1
                logger.warning(
                    "appointments.sync.list_failed",
                    merchant_id=str(loc.merchant_id),
                    error=str(e),
                )
                continue

            merchants += 1
            for ev in events:
                start_dt = _parse_iso(ev.get("start_iso"))
                if not ev.get("id") or start_dt is None:
                    continue
                contact_id = ev.get("contact_id")
                lead_id = None
                if contact_id:
                    lead = await leads.get_by_ghl_contact_id(
                        merchant_id=loc.merchant_id, ghl_contact_id=str(contact_id)
                    )
                    lead_id = lead.id if lead else None

                # Costruisci lo schedule e il prossimo reminder_due_at.
                ev_status = _map_status(ev.get("status"))
                if ev_status == "booked":
                    schedule = build_reminder_schedule(start_dt, reminder_lead_hours)
                    r_due_at = next_reminder_due(schedule)
                else:
                    # Appuntamento cancellato/noshow: nessun promemoria.
                    schedule = []
                    r_due_at = None

                await appts.upsert_by_ghl_id(
                    merchant_id=loc.merchant_id,
                    ghl_appointment_id=str(ev["id"]),
                    start_at=start_dt,
                    end_at=_parse_iso(ev.get("end_iso")),
                    lead_id=lead_id,
                    ghl_contact_id=str(contact_id) if contact_id else None,
                    calendar_id=ev.get("calendar_id") or str(calendar_id),
                    title=ev.get("title"),
                    status=ev_status,
                    source="ghl",
                    reminder_schedule=schedule,
                    reminder_due_at=r_due_at,
                )
                upserted += 1

    logger.info("appointments.sync.summary", merchants=merchants, upserted=upserted, failed=failed)
    return {"merchants": merchants, "upserted": upserted, "failed": failed}


async def _fetch_events(
    *,
    loc: ResolvedLocationToken,
    calendar_id: str,
    start_iso: str,
    end_iso: str,
    client_id: str,
    client_secret: str,
    kek: str,
) -> list[dict[str, Any]]:
    """Build a GHL client for one location and list its appointments.

    Persists any rotated token in its OWN committed transaction (GHL invalidates
    the old refresh token on rotation, so it must survive even if this run later
    fails) — same gotcha the booking handler guards against.
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
        client_id=client_id,
        client_secret=client_secret,
        on_token_refresh=_persist,
    )
    try:
        events: list[dict[str, Any]] = await client.list_appointments(
            calendar_id, start_iso=start_iso, end_iso=end_iso
        )
        return events
    finally:
        await client.close()


def _map_status(ghl_status: object | None) -> str:
    """Map GHL's appointmentStatus to our mirror status. Booked-ish states
    collapse to `booked`; everything else (cancelled/noshow/invalid) is kept
    verbatim, lowercased."""
    if not ghl_status:
        return "booked"
    value = str(ghl_status).strip().lower()
    if not value or value in _BOOKED_STATUSES:
        return "booked"
    return value


def _parse_iso(value: object | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
