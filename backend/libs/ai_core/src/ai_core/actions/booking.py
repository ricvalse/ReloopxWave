"""UC-02 — book_slot action handler.

The orchestrator emits a `book_slot` action when the conversation reaches a
booking intent. This handler:
  1. Resolves the merchant's GHL OAuth tokens.
  2. Picks the calendar (from payload or merchant config).
  3. Upserts the GHL contact for the lead.
  4. Attempts to create the booking at the preferred slot; if the slot is taken,
     fetches a few nearby alternatives and sends them back via WhatsApp.
  5. Persists lead.pipeline_stage_id + emits analytics.

Failures here do not block the main reply — the lead has already received a
text from the orchestrator. Booking confirmation is a separate WhatsApp message.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ai_core.orchestrator import OrchestratorAction
from config_resolver import ConfigKey, ConfigResolver
from db import (
    AnalyticsRepository,
    IntegrationRepository,
    LeadRepository,
    TenantContext,
    tenant_session,
)
from integrations.ghl.client import GHLClient, GHLTokenBundle
from shared import IntegrationError, get_logger

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class BookingOutcome:
    booked: bool
    booking_id: str | None
    slot_start_iso: str | None
    suggested: list[str]  # ISO starts for 3 alternatives if booking failed
    reason: str | None


class BookSlotHandler:
    """Dispatcher target for `kind == "book_slot"`."""

    kind = "book_slot"

    def __init__(
        self,
        *,
        kek_base64: str,
        ghl_client_id: str,
        ghl_client_secret: str,
        reply_sender,  # ai_core.ReplySender
    ) -> None:
        self._kek = kek_base64
        self._client_id = ghl_client_id
        self._client_secret = ghl_client_secret
        self._reply_sender = reply_sender

    async def __call__(self, action: OrchestratorAction, turn_ctx) -> None:
        worker_ctx = TenantContext(
            tenant_id=turn_ctx.tenant_id,
            merchant_id=turn_ctx.merchant_id,
            role="worker",
            actor_id=turn_ctx.merchant_id,
        )
        outcome: BookingOutcome | None = None

        async with tenant_session(worker_ctx) as session:
            ghl_repo = IntegrationRepository(session, kek_base64=self._kek)
            leads = LeadRepository(session)
            analytics = AnalyticsRepository(session)
            config = ConfigResolver(session)

            ghl = await ghl_repo.resolve_ghl(turn_ctx.merchant_id)
            if ghl is None:
                logger.warning("book_slot.no_ghl", merchant_id=str(turn_ctx.merchant_id))
                outcome = BookingOutcome(False, None, None, [], "no_ghl_integration")
            else:
                calendar_id = (
                    action.payload.get("calendar_id")
                    or await config.resolve(
                        ConfigKey.BOOKING_DEFAULT_CALENDAR_ID, merchant_id=turn_ctx.merchant_id
                    )
                )
                if not calendar_id:
                    outcome = BookingOutcome(False, None, None, [], "no_calendar_configured")
                else:
                    duration = int(
                        action.payload.get("duration_min")
                        or await config.resolve(
                            ConfigKey.BOOKING_DEFAULT_DURATION_MIN,
                            merchant_id=turn_ctx.merchant_id,
                        )
                        or 30
                    )
                    outcome = await self._try_book(
                        ghl=ghl,
                        calendar_id=calendar_id,
                        duration_min=duration,
                        contact_phone=turn_ctx.lead_phone,
                        contact_fields=action.payload.get("contact_fields", {}),
                        preferred_start_iso=action.payload.get("preferred_start_iso"),
                    )

            if outcome and outcome.booked and outcome.booking_id:
                await leads.update_score(turn_ctx.lead_id, score=100, reasons=["booked"])

            await analytics.emit(
                tenant_id=turn_ctx.tenant_id,
                merchant_id=turn_ctx.merchant_id,
                event_type="booking.created" if (outcome and outcome.booked) else "booking.failed",
                subject_type="lead",
                subject_id=turn_ctx.lead_id,
                properties={
                    "reason": outcome.reason if outcome else "unknown",
                    "slot_start_iso": outcome.slot_start_iso if outcome else None,
                    "suggested": outcome.suggested if outcome else [],
                    "conversation_id": str(turn_ctx.conversation_id),
                },
            )

        # Confirmation message is a separate WhatsApp send. Keep it short.
        await self._send_confirmation(turn_ctx, outcome)

    async def _try_book(
        self,
        *,
        ghl,
        calendar_id: str,
        duration_min: int,
        contact_phone: str,
        contact_fields: dict[str, Any],
        preferred_start_iso: str | None,
    ) -> BookingOutcome:
        client = GHLClient(
            token_bundle=GHLTokenBundle(
                access_token=ghl.access_token,
                refresh_token=ghl.refresh_token,
                expires_at=ghl.expires_at,
                location_id=ghl.location_id,
            ),
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        try:
            contact = await client.upsert_contact(
                {
                    "phone": contact_phone,
                    "email": contact_fields.get("email"),
                    "firstName": contact_fields.get("first_name") or contact_fields.get("name"),
                    "lastName": contact_fields.get("last_name"),
                }
            )
            contact_id = contact.get("contact", {}).get("id") or contact.get("id")
            if not contact_id:
                return BookingOutcome(False, None, None, [], "contact_upsert_failed")

            slot_start = _parse_iso(preferred_start_iso) or _next_business_hour()
            slot_end = slot_start + timedelta(minutes=duration_min)

            try:
                booking = await client.create_booking(
                    calendar_id,
                    contact_id=contact_id,
                    slot_start_iso=slot_start.isoformat(),
                    slot_end_iso=slot_end.isoformat(),
                )
                booking_id = booking.get("id") or booking.get("event", {}).get("id")
                return BookingOutcome(True, booking_id, slot_start.isoformat(), [], "booked")
            except IntegrationError:
                # Propose alternatives
                window_start = slot_start - timedelta(hours=2)
                window_end = slot_start + timedelta(days=3)
                slots = await client.get_free_slots(
                    calendar_id,
                    start_iso=window_start.isoformat(),
                    end_iso=window_end.isoformat(),
                )
                suggestions = [s.get("startTime") or s.get("start") for s in slots[:3] if s]
                suggestions = [s for s in suggestions if s]
                return BookingOutcome(False, None, None, suggestions, "slot_taken")
        finally:
            await client.close()

    async def _send_confirmation(self, turn_ctx, outcome: BookingOutcome | None) -> None:
        if outcome is None:
            return
        if outcome.booked and outcome.slot_start_iso:
            text = (
                f"Perfetto, ho prenotato per te l'appuntamento del "
                f"{_format_human(outcome.slot_start_iso)}. Ti invieremo il promemoria."
            )
        elif outcome.suggested:
            options = "\n".join(f"• {_format_human(s)}" for s in outcome.suggested)
            text = (
                "Quello slot non è più disponibile. Ti suggerisco:\n"
                f"{options}\nFammi sapere quale preferisci."
            )
        else:
            text = (
                "Al momento non riesco a completare la prenotazione. "
                "Ti ricontatteremo a brevissimo."
            )
        await self._reply_sender.send(
            access_token=turn_ctx.whatsapp_access_token,
            phone_number_id=turn_ctx.phone_number_id,
            to_phone=turn_ctx.lead_phone,
            text=text,
        )


# ---- helpers --------------------------------------------------------------

def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _next_business_hour() -> datetime:
    """Fallback: same-day next full hour during business hours, or 9:00 tomorrow."""
    now = datetime.now(tz=timezone.utc)
    candidate = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    if candidate.hour < 9:
        candidate = candidate.replace(hour=9)
    elif candidate.hour >= 18:
        candidate = (candidate + timedelta(days=1)).replace(hour=9)
    return candidate


def _format_human(iso: str) -> str:
    dt = _parse_iso(iso)
    if dt is None:
        return iso
    return dt.strftime("%d/%m alle %H:%M")
