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

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ai_core.automations import resolve_send_plan
from ai_core.orchestrator import OrchestratorAction
from config_resolver import ConfigKey, ConfigResolver
from db import (
    FLOW_BOOKING_REMINDER,
    AnalyticsRepository,
    AppointmentRepository,
    AutomationRepository,
    GHLMarketplaceRepository,
    GhlSyncRepository,
    IntegrationRepository,
    LeadRepository,
    TenantContext,
    build_reminder_schedule,
    next_reminder_due,
    session_scope,
    tenant_session,
)
from db.repositories.services import BusinessHourRepository, ServiceRepository
from integrations.ghl.client import GHLClient, GHLTokenBundle, build_contact_custom_fields
from shared import IntegrationError, get_logger

if TYPE_CHECKING:
    from ai_core.conversation_service import ReplySender, TurnContext

logger = get_logger(__name__)


async def _resolve_reminder_lead_hours(
    session: Any,
    *,
    merchant_id: Any,
    config: ConfigResolver,
    fallback: list[int],
) -> list[int]:
    """Ore-di-anticipo dei promemoria appuntamento (ADR 0011).

    Precedenza: grafo del system flow `booking_reminder` se ABILITATO (ore dei
    nodi `wait_until_before`) → ConfigKey `booking.reminder_schedule` → `fallback`.
    Così il merchant può configurare gli anticipi dalla lavagnetta; se il flusso è
    disabilitato/assente, vale la card numerica (compat).
    """
    flow = await AutomationRepository(session).get_by_system_key(merchant_id, FLOW_BOOKING_REMINDER)
    if flow is not None and flow.enabled:
        nodes = [
            {"node_key": n.node_key, "kind": n.kind, "type": n.type, "config": n.config or {}}
            for n in flow.nodes
        ]
        edges = [
            {"source_key": e.source_key, "target_key": e.target_key, "branch": e.branch}
            for e in flow.edges
        ]
        plan = resolve_send_plan(nodes, edges, context={})
        hours = sorted(
            {s.anchor_hours_before for s in plan.sends if s.anchor_hours_before},
            reverse=True,
        )
        if hours:
            return list(hours)
    raw = await config.resolve(ConfigKey.BOOKING_REMINDER_SCHEDULE, merchant_id=merchant_id)
    return list(raw) if raw else list(fallback)


@dataclass(slots=True, frozen=True)
class BookingOutcome:
    booked: bool
    booking_id: str | None
    slot_start_iso: str | None
    suggested: list[str]  # ISO starts for 3 alternatives if booking failed
    reason: str | None
    # GHL opportunity stamped by the booking handler — propagated up so the
    # outer `__call__` can persist it on `leads.meta`. UC-04 (move_pipeline)
    # reads it back from there when the orchestrator emits the action without
    # a payload.
    opportunity_id: str | None = None
    pipeline_id: str | None = None
    # Carried up from `_try_book` on success so `__call__` can persist the local
    # appointment mirror (UC-02) without re-deriving the slot/contact/calendar.
    slot_end_iso: str | None = None
    contact_id: str | None = None
    calendar_id: str | None = None
    tz_name: str | None = None
    # True quando GHL non è disponibile e l'appointment è stato salvato solo
    # nell'agenda interna (ghl_appointment_id=NULL).
    local_only: bool = False


class BookSlotHandler:
    """Dispatcher target for `kind == "book_slot"`."""

    kind = "book_slot"

    def __init__(
        self,
        *,
        kek_base64: str,
        ghl_client_id: str,
        ghl_client_secret: str,
        reply_sender: ReplySender,
    ) -> None:
        self._kek = kek_base64
        self._client_id = ghl_client_id
        self._client_secret = ghl_client_secret
        self._reply_sender = reply_sender

    async def __call__(self, action: OrchestratorAction, turn_ctx: TurnContext) -> None:
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
            ghl_sync = GhlSyncRepository(session)
            config = ConfigResolver(session)

            # Default reminder schedule (ore di anticipo); aggiornato sotto
            # se il merchant ha una configurazione personalizzata.
            reminder_lead_hours: list[int] = [24]

            ghl = await ghl_repo.resolve_ghl(turn_ctx.merchant_id)

            # Resolve the chosen service. When the merchant has configured a
            # bookable-services catalog, booking is GATED on a valid active
            # service: the agent must not invent appointments for things that
            # aren't offered (the LLM emits free-text slots, so the menu is the
            # only real constraint). Merchants with NO services configured keep
            # the legacy default-duration behaviour — we can't gate against an
            # empty menu without bricking booking before services are set up.
            service_id_raw = action.payload.get("service_id")
            _svc_duration: int | None = None
            _svc_calendar_id: str | None = None
            _svc_id: uuid.UUID | None = None
            _svc_name: str | None = None
            _svc_repo = ServiceRepository(session)

            _active_services: list[Any] = []
            try:
                _active_services = await _svc_repo.list(turn_ctx.merchant_id)
            except Exception:
                _active_services = []

            _svc = None
            if service_id_raw:
                try:
                    _svc = await _svc_repo.get(turn_ctx.merchant_id, uuid.UUID(str(service_id_raw)))
                except Exception:
                    _svc = None

            if _svc is not None and _svc.is_active:
                _svc_id = _svc.id
                _svc_name = _svc.name
                _svc_duration = _svc.duration_min
                if _svc.ghl_calendar_id:
                    _svc_calendar_id = _svc.ghl_calendar_id
            elif _active_services:
                # Merchant offers services but the agent didn't pick a valid one
                # → refuse to book. No calendar write, no false confirmation:
                # ask the lead which service they want; the next turn re-issues
                # book_slot with a real service_id.
                logger.info(
                    "book_slot.service_required",
                    merchant_id=str(turn_ctx.merchant_id),
                    service_id=str(service_id_raw) if service_id_raw else None,
                )
                await analytics.emit(
                    tenant_id=turn_ctx.tenant_id,
                    merchant_id=turn_ctx.merchant_id,
                    event_type="booking.failed",
                    subject_type="lead",
                    subject_id=turn_ctx.lead_id,
                    variant_id=turn_ctx.variant_id,
                    properties={
                        "reason": "service_required",
                        "service_id": str(service_id_raw) if service_id_raw else None,
                        "conversation_id": str(turn_ctx.conversation_id),
                    },
                )
                await self._reply_sender.send(
                    phone_number_id=turn_ctx.phone_number_id,
                    api_key=turn_ctx.api_key,
                    to_phone=turn_ctx.lead_phone,
                    text=format_service_selection(_active_services),
                    waba_base_url=turn_ctx.waba_base_url,
                )
                return

            # Load business hours for smart fallback slot selection.
            _business_hours: list[Any] = []
            try:
                _business_hours = await BusinessHourRepository(session).list(turn_ctx.merchant_id)
            except Exception:
                pass

            if ghl is None:
                # GHL non connesso: salva l'appuntamento nell'agenda locale.
                logger.info("book_slot.local_only", merchant_id=str(turn_ctx.merchant_id))
                tz_name_local = str(
                    await config.resolve(
                        ConfigKey.SCHEDULE_TIMEZONE, merchant_id=turn_ctx.merchant_id
                    )
                    or "Europe/Rome"
                )
                duration_local = int(
                    _svc_duration
                    or action.payload.get("duration_min")
                    or await config.resolve(
                        ConfigKey.BOOKING_DEFAULT_DURATION_MIN,
                        merchant_id=turn_ctx.merchant_id,
                    )
                    or 30
                )
                reminder_lead_hours = await _resolve_reminder_lead_hours(
                    session,
                    merchant_id=turn_ctx.merchant_id,
                    config=config,
                    fallback=reminder_lead_hours,
                )

                tz_local = _resolve_tz(tz_name_local)
                preferred_iso_local = action.payload.get("preferred_start_iso")
                start_dt_local = _parse_iso(preferred_iso_local, tz_local) or _next_business_hour(
                    tz_local, business_hours=_business_hours
                )
                end_dt_local = start_dt_local + timedelta(minutes=duration_local)
                slot_start_iso_local = start_dt_local.isoformat()
                slot_end_iso_local = end_dt_local.isoformat()

                try:
                    r_schedule_local = build_reminder_schedule(start_dt_local, reminder_lead_hours)
                    r_due_at_local = next_reminder_due(r_schedule_local)
                    async with session.begin_nested():
                        await AppointmentRepository(session).record_booking(
                            merchant_id=turn_ctx.merchant_id,
                            lead_id=turn_ctx.lead_id,
                            ghl_appointment_id=None,
                            ghl_contact_id=None,
                            calendar_id=None,
                            start_at=start_dt_local,
                            end_at=end_dt_local,
                            tz_name=tz_name_local,
                            title=_svc_name,
                            service_id=_svc_id,
                            status="booked",
                            source="bot_local",
                            reminder_schedule=r_schedule_local,
                            reminder_due_at=r_due_at_local,
                        )
                    await leads.update_score(turn_ctx.lead_id, score=100, reasons=["booked"])
                except Exception as e:
                    logger.warning("book_slot.local_write_failed", error=str(e))

                outcome = BookingOutcome(
                    True,
                    None,
                    slot_start_iso_local,
                    [],
                    "local_only",
                    slot_end_iso=slot_end_iso_local,
                    tz_name=tz_name_local,
                    local_only=True,
                )
            else:
                calendar_id = (
                    _svc_calendar_id
                    or action.payload.get("calendar_id")
                    or await config.resolve(
                        ConfigKey.BOOKING_DEFAULT_CALENDAR_ID, merchant_id=turn_ctx.merchant_id
                    )
                )
                if not calendar_id:
                    # GHL connesso ma nessun calendario configurato — salva nell'agenda interna.
                    logger.info("book_slot.no_calendar_ghl", merchant_id=str(turn_ctx.merchant_id))
                    _tz_name = str(
                        await config.resolve(
                            ConfigKey.SCHEDULE_TIMEZONE, merchant_id=turn_ctx.merchant_id
                        )
                        or "Europe/Rome"
                    )
                    _dur = int(
                        _svc_duration
                        or action.payload.get("duration_min")
                        or await config.resolve(
                            ConfigKey.BOOKING_DEFAULT_DURATION_MIN,
                            merchant_id=turn_ctx.merchant_id,
                        )
                        or 30
                    )
                    reminder_lead_hours = await _resolve_reminder_lead_hours(
                        session,
                        merchant_id=turn_ctx.merchant_id,
                        config=config,
                        fallback=reminder_lead_hours,
                    )
                    _tz = _resolve_tz(_tz_name)
                    _start = _parse_iso(
                        action.payload.get("preferred_start_iso"), _tz
                    ) or _next_business_hour(_tz, business_hours=_business_hours)
                    _end = _start + timedelta(minutes=_dur)
                    try:
                        _rs = build_reminder_schedule(_start, reminder_lead_hours)
                        _rd = next_reminder_due(_rs)
                        async with session.begin_nested():
                            await AppointmentRepository(session).record_booking(
                                merchant_id=turn_ctx.merchant_id,
                                lead_id=turn_ctx.lead_id,
                                ghl_appointment_id=None,
                                ghl_contact_id=None,
                                calendar_id=None,
                                start_at=_start,
                                end_at=_end,
                                tz_name=_tz_name,
                                title=_svc_name,
                                service_id=_svc_id,
                                status="booked",
                                source="bot_local",
                                reminder_schedule=_rs,
                                reminder_due_at=_rd,
                            )
                        await leads.update_score(turn_ctx.lead_id, score=100, reasons=["booked"])
                    except Exception as e:
                        logger.warning("book_slot.local_write_failed", error=str(e))
                    outcome = BookingOutcome(
                        True,
                        None,
                        _start.isoformat(),
                        [],
                        "local_only",
                        slot_end_iso=_end.isoformat(),
                        tz_name=_tz_name,
                        local_only=True,
                    )
                else:
                    duration = int(
                        _svc_duration
                        or action.payload.get("duration_min")
                        or await config.resolve(
                            ConfigKey.BOOKING_DEFAULT_DURATION_MIN,
                            merchant_id=turn_ctx.merchant_id,
                        )
                        or 30
                    )
                    pipeline_id = await config.resolve(
                        ConfigKey.PIPELINE_DEFAULT_PIPELINE_ID,
                        merchant_id=turn_ctx.merchant_id,
                    )
                    new_stage_id = await config.resolve(
                        ConfigKey.PIPELINE_NEW_STAGE_ID,
                        merchant_id=turn_ctx.merchant_id,
                    )
                    tz_name = await config.resolve(
                        ConfigKey.SCHEDULE_TIMEZONE, merchant_id=turn_ctx.merchant_id
                    )
                    lookahead_raw = action.payload.get("lookahead_days") or await config.resolve(
                        ConfigKey.BOOKING_LOOKAHEAD_DAYS, merchant_id=turn_ctx.merchant_id
                    )
                    lookahead_days = int(lookahead_raw) if lookahead_raw else 14

                    # Risolvi gli anticipi multi-reminder: grafo (system flow) → config.
                    reminder_lead_hours = await _resolve_reminder_lead_hours(
                        session,
                        merchant_id=turn_ctx.merchant_id,
                        config=config,
                        fallback=reminder_lead_hours,
                    )

                    # CRM sync extras (capitolato sez.5): map collected lead data
                    # to GHL custom fields + apply default tags. Merge the action
                    # payload's contact_fields over turn_ctx.collected_data so the
                    # freshest values win.
                    contact_fields = action.payload.get("contact_fields", {})
                    merged_values = {
                        **(turn_ctx.collected_data or {}),
                        **contact_fields,
                    }
                    field_map = await config.resolve(
                        ConfigKey.GHL_CONTACT_FIELD_MAP, merchant_id=turn_ctx.merchant_id
                    )
                    custom_fields = build_contact_custom_fields(
                        dict(field_map or {}), merged_values
                    )
                    default_tags = await config.resolve(
                        ConfigKey.GHL_CONTACT_DEFAULT_TAGS, merchant_id=turn_ctx.merchant_id
                    )
                    payload_tags = action.payload.get("tags") or []
                    tags = list(default_tags or []) + list(payload_tags)

                    async def _persist_tokens(bundle: GHLTokenBundle) -> None:
                        # Persist the rotated location bundle in its OWN committed
                        # transaction. The handler's `session` rolls back if a
                        # later GHL call raises — but GHL has already invalidated
                        # the old refresh token, so the new one must survive that
                        # rollback or the integration breaks permanently.
                        if not bundle.location_id:
                            return
                        async with session_scope() as token_session:
                            await GHLMarketplaceRepository(
                                token_session, kek_base64=self._kek
                            ).set_location_token(
                                location_id=bundle.location_id,
                                access_token=bundle.access_token,
                                refresh_token=bundle.refresh_token,
                                expires_at=bundle.expires_at,
                            )

                    outcome = await self._try_book(
                        ghl=ghl,
                        calendar_id=calendar_id,
                        duration_min=duration,
                        contact_phone=turn_ctx.lead_phone,
                        contact_fields=contact_fields,
                        preferred_start_iso=action.payload.get("preferred_start_iso"),
                        pipeline_id=str(pipeline_id) if pipeline_id else None,
                        new_stage_id=str(new_stage_id) if new_stage_id else None,
                        tz_name=str(tz_name) if tz_name else "Europe/Rome",
                        lookahead_days=lookahead_days,
                        custom_fields=custom_fields,
                        tags=tags,
                        on_token_refresh=_persist_tokens,
                        ghl_sync=ghl_sync,
                        tenant_id=turn_ctx.tenant_id,
                        merchant_id=turn_ctx.merchant_id,
                        lead_id=turn_ctx.lead_id,
                        conversation_id=turn_ctx.conversation_id,
                    )

            # GHL ha fallito per errore transitorio ma lo slot era confermato — salva internamente.
            if outcome and outcome.reason == "booking_error" and outcome.slot_start_iso:
                try:
                    _tz = _resolve_tz(outcome.tz_name or "Europe/Rome")
                    _start = _parse_iso(outcome.slot_start_iso, _tz)
                    _end = (
                        _parse_iso(outcome.slot_end_iso, _tz) if outcome.slot_end_iso else None
                    ) or (_start + timedelta(minutes=30) if _start else None)
                    if _start is not None:
                        _rs = build_reminder_schedule(_start, reminder_lead_hours)
                        _rd = next_reminder_due(_rs)
                        async with session.begin_nested():
                            await AppointmentRepository(session).record_booking(
                                merchant_id=turn_ctx.merchant_id,
                                lead_id=turn_ctx.lead_id,
                                ghl_appointment_id=None,
                                ghl_contact_id=outcome.contact_id,
                                calendar_id=None,
                                start_at=_start,
                                end_at=_end,
                                tz_name=outcome.tz_name,
                                title=_svc_name,
                                service_id=_svc_id,
                                status="booked",
                                source="bot_local",
                                reminder_schedule=_rs,
                                reminder_due_at=_rd,
                            )
                        await leads.update_score(turn_ctx.lead_id, score=100, reasons=["booked"])
                        outcome = BookingOutcome(
                            True,
                            None,
                            outcome.slot_start_iso,
                            [],
                            "local_only",
                            opportunity_id=outcome.opportunity_id,
                            pipeline_id=outcome.pipeline_id,
                            slot_end_iso=outcome.slot_end_iso,
                            contact_id=outcome.contact_id,
                            tz_name=outcome.tz_name,
                            local_only=True,
                        )
                except Exception as e:
                    logger.warning("book_slot.local_write_failed", error=str(e))

            if outcome and outcome.booked and outcome.booking_id:
                await leads.update_score(turn_ctx.lead_id, score=100, reasons=["booked"])
                # Write-through mirror of the GHL appointment. GHL stays source
                # of truth; the local row preserves the GHL appointment_id (the
                # live flow otherwise drops it) so reschedule/cancel/reconcile
                # have a join handle, and powers the merchant agenda. Best-effort:
                # the calendar slot already exists in GHL and the lead gets a
                # confirmation regardless, so a mirror-write hiccup must not crash
                # the turn or swallow the confirmation.
                try:
                    tz = _resolve_tz(outcome.tz_name or "Europe/Rome")
                    start_dt = _parse_iso(outcome.slot_start_iso, tz)
                    end_dt = _parse_iso(outcome.slot_end_iso, tz)
                    if start_dt is not None:
                        r_schedule = build_reminder_schedule(start_dt, reminder_lead_hours)
                        r_due_at = next_reminder_due(r_schedule)
                        await AppointmentRepository(session).record_booking(
                            merchant_id=turn_ctx.merchant_id,
                            lead_id=turn_ctx.lead_id,
                            ghl_appointment_id=outcome.booking_id,
                            ghl_contact_id=outcome.contact_id,
                            calendar_id=outcome.calendar_id,
                            start_at=start_dt,
                            end_at=end_dt,
                            tz_name=outcome.tz_name,
                            title=_svc_name,
                            service_id=_svc_id,
                            source="bot",
                            reminder_schedule=r_schedule,
                            reminder_due_at=r_due_at,
                        )
                except Exception as e:  # pragma: no cover — mirror write is best-effort
                    logger.warning("book_slot.mirror_failed", error=str(e))

            if outcome and outcome.opportunity_id:
                # Stash on lead.meta so MovePipelineHandler can find the
                # opportunity later without having to call GHL again.
                lead_row = await leads.get_by_phone(
                    merchant_id=turn_ctx.merchant_id, phone=turn_ctx.lead_phone
                )
                if lead_row is not None:
                    meta = dict(lead_row.meta or {})
                    meta["ghl_opportunity_id"] = outcome.opportunity_id
                    if outcome.pipeline_id:
                        meta["ghl_pipeline_id"] = outcome.pipeline_id
                    lead_row.meta = meta

            # Persist any contact identity the bot collected (fill-only) so it
            # survives on the lead — feeds UC-05 scoring and the UC-04 note even
            # when no opportunity was created this turn.
            cf = action.payload.get("contact_fields", {})
            if cf.get("name") or cf.get("first_name") or cf.get("email"):
                lead_row = await leads.get_by_phone(
                    merchant_id=turn_ctx.merchant_id, phone=turn_ctx.lead_phone
                )
                if lead_row is not None:
                    await leads.update_contact_fields(
                        lead_row.id,
                        name=cf.get("name") or cf.get("first_name"),
                        email=cf.get("email"),
                    )

            await analytics.emit(
                tenant_id=turn_ctx.tenant_id,
                merchant_id=turn_ctx.merchant_id,
                event_type="booking.created" if (outcome and outcome.booked) else "booking.failed",
                subject_type="lead",
                subject_id=turn_ctx.lead_id,
                variant_id=turn_ctx.variant_id,
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
        ghl: Any,
        calendar_id: str,
        duration_min: int,
        contact_phone: str,
        contact_fields: dict[str, Any],
        preferred_start_iso: str | None,
        pipeline_id: str | None,
        new_stage_id: str | None,
        tz_name: str = "Europe/Rome",
        lookahead_days: int = 14,
        custom_fields: list[dict[str, Any]] | None = None,
        tags: list[str] | None = None,
        on_token_refresh: Callable[[GHLTokenBundle], Awaitable[None]] | None = None,
        ghl_sync: GhlSyncRepository | None = None,
        tenant_id: Any | None = None,
        merchant_id: Any | None = None,
        lead_id: Any | None = None,
        conversation_id: Any | None = None,
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
            on_token_refresh=on_token_refresh,
        )
        _sync_kwargs: dict[str, Any] = {
            "tenant_id": tenant_id,
            "merchant_id": merchant_id,
            "lead_id": lead_id,
            "conversation_id": conversation_id,
        }

        async def _log_sync(
            operation: str,
            ghl_entity_type: str,
            ghl_entity_id: str | None,
            status: str = "success",
            payload: dict[str, Any] | None = None,
            result: dict[str, Any] | None = None,
            error_detail: str | None = None,
        ) -> None:
            if ghl_sync is not None:
                try:
                    await ghl_sync.emit(
                        operation=operation,
                        ghl_entity_type=ghl_entity_type,
                        ghl_entity_id=ghl_entity_id,
                        status=status,
                        payload=payload,
                        result=result,
                        error_detail=error_detail,
                        **_sync_kwargs,
                    )
                except Exception as _e:
                    logger.warning("ghl_sync.emit_failed", error=str(_e), operation=operation)

        try:
            contact_payload: dict[str, Any] = {
                "phone": contact_phone,
                "email": contact_fields.get("email"),
                "firstName": contact_fields.get("first_name") or contact_fields.get("name"),
                "lastName": contact_fields.get("last_name"),
            }
            contact = await client.upsert_contact(
                contact_payload,
                custom_fields=custom_fields,
                tags=tags,
            )
            contact_id = contact.get("contact", {}).get("id") or contact.get("id")
            if not contact_id:
                await _log_sync(
                    "contact.upserted",
                    "contact",
                    None,
                    status="error",
                    error_detail="no contact_id in response",
                    payload=contact_payload,
                )
                return BookingOutcome(False, None, None, [], "contact_upsert_failed")
            await _log_sync(
                "contact.upserted",
                "contact",
                contact_id,
                payload={k: v for k, v in contact_payload.items() if v},
                result={"id": contact_id},
            )

            opportunity_id = await self._ensure_opportunity(
                client=client,
                ghl_location_id=ghl.location_id,
                contact_id=contact_id,
                contact_fields=contact_fields,
                pipeline_id=pipeline_id,
                new_stage_id=new_stage_id,
                log_sync=_log_sync,
            )

            tz = _resolve_tz(tz_name)
            slot_start = _parse_iso(preferred_start_iso, tz) or _next_business_hour(tz)
            slot_end = slot_start + timedelta(minutes=duration_min)

            booking_payload: dict[str, Any] = {
                "calendar_id": calendar_id,
                "contact_id": contact_id,
                "slot_start_iso": slot_start.isoformat(),
                "slot_end_iso": slot_end.isoformat(),
            }
            try:
                booking = await client.create_booking(
                    calendar_id,
                    contact_id=contact_id,
                    slot_start_iso=slot_start.isoformat(),
                    slot_end_iso=slot_end.isoformat(),
                )
                booking_id = booking.get("id") or booking.get("event", {}).get("id")
                await _log_sync(
                    "booking.created",
                    "appointment",
                    str(booking_id) if booking_id else None,
                    payload=booking_payload,
                    result={"id": booking_id},
                )
                return BookingOutcome(
                    True,
                    booking_id,
                    slot_start.isoformat(),
                    [],
                    "booked",
                    opportunity_id=opportunity_id,
                    pipeline_id=pipeline_id if opportunity_id else None,
                    slot_end_iso=slot_end.isoformat(),
                    contact_id=contact_id,
                    calendar_id=calendar_id,
                    tz_name=tz_name,
                )
            except IntegrationError as e:
                # A transient server error (5xx) is NOT a slot conflict — querying
                # free slots would likely fail too, and proposing alternatives
                # would be misleading. Fall back gracefully ("ti ricontatteremo").
                if not _is_slot_conflict(e):
                    await _log_sync(
                        "booking.created",
                        "appointment",
                        None,
                        status="error",
                        error_detail=str(e),
                        payload=booking_payload,
                    )
                    return BookingOutcome(
                        False,
                        None,
                        slot_start.isoformat(),
                        [],
                        "booking_error",
                        opportunity_id=opportunity_id,
                        pipeline_id=pipeline_id if opportunity_id else None,
                        slot_end_iso=slot_end.isoformat(),
                        contact_id=contact_id,
                        tz_name=tz_name,
                    )
                # Slot taken / unavailable (4xx) → propose alternatives within the
                # merchant's configured booking lookahead window.
                window_start = slot_start - timedelta(hours=2)
                window_end = slot_start + timedelta(days=lookahead_days)
                slots = await client.get_free_slots(
                    calendar_id,
                    start_iso=window_start.isoformat(),
                    end_iso=window_end.isoformat(),
                )
                raw_suggestions = [s.get("startTime") or s.get("start") for s in slots[:3] if s]
                suggestions: list[str] = [s for s in raw_suggestions if s]
                await _log_sync(
                    "booking.created",
                    "appointment",
                    None,
                    status="error",
                    error_detail="slot_taken",
                    payload=booking_payload,
                    result={"suggested_slots": suggestions},
                )
                return BookingOutcome(
                    False,
                    None,
                    None,
                    suggestions,
                    "slot_taken",
                    opportunity_id=opportunity_id,
                    pipeline_id=pipeline_id if opportunity_id else None,
                )
        finally:
            await client.close()

    async def _ensure_opportunity(
        self,
        *,
        client: GHLClient,
        ghl_location_id: str | None,
        contact_id: str,
        contact_fields: dict[str, Any],
        pipeline_id: str | None,
        new_stage_id: str | None,
        log_sync: Any = None,
    ) -> str | None:
        """Find or create a GHL opportunity for this contact.

        Returns the opportunity id when one is in scope, None when the merchant
        hasn't configured a default pipeline/stage yet (we don't want to silently
        guess which pipeline a stray opportunity should land in). The booking
        itself doesn't depend on this call — the calendar slot is created either
        way; this only enables UC-04 to move the pipeline later.
        """
        if not ghl_location_id or not pipeline_id or not new_stage_id:
            return None
        try:
            existing = await client.search_opportunities_by_contact(
                contact_id, location_id=ghl_location_id
            )
            for opp in existing:
                if opp.get("pipelineId") == pipeline_id:
                    opp_id = opp.get("id")
                    if isinstance(opp_id, str):
                        return opp_id
            name = contact_fields.get("name") or contact_fields.get("first_name") or "Lead WhatsApp"
            opp_payload: dict[str, Any] = {
                "pipeline_id": pipeline_id,
                "stage_id": new_stage_id,
                "contact_id": contact_id,
                "name": str(name),
            }
            created = await client.create_opportunity(
                pipeline_id=pipeline_id,
                stage_id=new_stage_id,
                contact_id=contact_id,
                location_id=ghl_location_id,
                name=str(name),
            )
            opp_id = created.get("id") or created.get("opportunity", {}).get("id")
            if log_sync is not None and isinstance(opp_id, str):
                await log_sync(
                    "opportunity.created",
                    "opportunity",
                    opp_id,
                    payload=opp_payload,
                    result={"id": opp_id},
                )
            return opp_id if isinstance(opp_id, str) else None
        except IntegrationError as e:
            logger.warning("book_slot.opportunity_failed", error=str(e))
            if log_sync is not None:
                await log_sync(
                    "opportunity.created",
                    "opportunity",
                    None,
                    status="error",
                    error_detail=str(e),
                )
            return None

    async def _send_confirmation(
        self, turn_ctx: TurnContext, outcome: BookingOutcome | None
    ) -> None:
        if outcome is None:
            return
        text = format_booking_confirmation(
            booked=outcome.booked,
            slot_start_iso=outcome.slot_start_iso,
            suggested=outcome.suggested,
            local_only=outcome.local_only,
        )
        await self._reply_sender.send(
            phone_number_id=turn_ctx.phone_number_id,
            api_key=turn_ctx.api_key,
            to_phone=turn_ctx.lead_phone,
            text=text,
            waba_base_url=turn_ctx.waba_base_url,
        )


class ProposeSlotsHandler:
    """UC-02 — proactively offer free calendar slots when the lead wants to book
    but hasn't named a time. Read-only: fetches availability and messages the top
    few options; the lead's pick then drives a `book_slot` on the next turn."""

    kind = "propose_slots"

    def __init__(
        self,
        *,
        kek_base64: str,
        ghl_client_id: str,
        ghl_client_secret: str,
        reply_sender: ReplySender,
    ) -> None:
        self._kek = kek_base64
        self._client_id = ghl_client_id
        self._client_secret = ghl_client_secret
        self._reply_sender = reply_sender

    async def __call__(self, action: OrchestratorAction, turn_ctx: TurnContext) -> None:
        worker_ctx = TenantContext(
            tenant_id=turn_ctx.tenant_id,
            merchant_id=turn_ctx.merchant_id,
            role="worker",
            actor_id=turn_ctx.merchant_id,
        )
        suggestions: list[str] = []
        async with tenant_session(worker_ctx) as session:
            ghl = await IntegrationRepository(session, kek_base64=self._kek).resolve_ghl(
                turn_ctx.merchant_id
            )
            if ghl is None:
                return
            config = ConfigResolver(session)
            calendar_id = action.payload.get("calendar_id") or await config.resolve(
                ConfigKey.BOOKING_DEFAULT_CALENDAR_ID, merchant_id=turn_ctx.merchant_id
            )
            if not calendar_id:
                return
            tz_name = (
                await config.resolve(ConfigKey.SCHEDULE_TIMEZONE, merchant_id=turn_ctx.merchant_id)
                or "Europe/Rome"
            )
            lookahead = action.payload.get("lookahead_days") or await config.resolve(
                ConfigKey.BOOKING_LOOKAHEAD_DAYS, merchant_id=turn_ctx.merchant_id
            )

            async def _persist(bundle: GHLTokenBundle) -> None:
                if not bundle.location_id:
                    return
                async with session_scope() as token_session:
                    await GHLMarketplaceRepository(
                        token_session, kek_base64=self._kek
                    ).set_location_token(
                        location_id=bundle.location_id,
                        access_token=bundle.access_token,
                        refresh_token=bundle.refresh_token,
                        expires_at=bundle.expires_at,
                    )

            suggestions = await self._fetch_slots(
                ghl=ghl,
                calendar_id=str(calendar_id),
                tz_name=str(tz_name),
                lookahead_days=int(lookahead) if lookahead else 14,
                on_token_refresh=_persist,
            )

        if suggestions:
            await self._reply_sender.send(
                phone_number_id=turn_ctx.phone_number_id,
                api_key=turn_ctx.api_key,
                to_phone=turn_ctx.lead_phone,
                text=format_slot_proposal(suggestions),
                waba_base_url=turn_ctx.waba_base_url,
            )

    async def _fetch_slots(
        self,
        *,
        ghl: Any,
        calendar_id: str,
        tz_name: str,
        lookahead_days: int,
        on_token_refresh: Callable[[GHLTokenBundle], Awaitable[None]] | None = None,
    ) -> list[str]:
        client = GHLClient(
            token_bundle=GHLTokenBundle(
                access_token=ghl.access_token,
                refresh_token=ghl.refresh_token,
                expires_at=ghl.expires_at,
                location_id=ghl.location_id,
            ),
            client_id=self._client_id,
            client_secret=self._client_secret,
            on_token_refresh=on_token_refresh,
        )
        try:
            tz = _resolve_tz(tz_name)
            start = _next_business_hour(tz)
            end = start + timedelta(days=lookahead_days)
            slots = await client.get_free_slots(
                calendar_id, start_iso=start.isoformat(), end_iso=end.isoformat()
            )
            raw = [s.get("startTime") or s.get("start") for s in slots[:3] if s]
            return [s for s in raw if s]
        except IntegrationError:
            return []
        finally:
            await client.close()


# ---- helpers --------------------------------------------------------------


def format_slot_proposal(suggested: list[str]) -> str:
    """Italian WhatsApp text offering free slots (UC-02 proactive proposal)."""
    options = "\n".join(f"• {_format_human(s)}" for s in suggested)
    return f"Ecco le prime disponibilità:\n{options}\nFammi sapere quale preferisci."


def format_service_selection(services: list[Any]) -> str:
    """Italian WhatsApp text asking the lead to pick a bookable service.

    Sent when the merchant has a service catalog but the agent tried to book
    without naming a valid service — the booking gate refuses and we surface
    the real menu so the lead can choose (no appointment is created)."""
    options = "\n".join(f"• {name}" for s in services if (name := getattr(s, "name", None)))
    if not options:
        return "Per quale servizio vuoi prenotare?"
    return f"Per fissare l'appuntamento, quale servizio ti interessa?\n{options}"


def _is_slot_conflict(e: IntegrationError) -> bool:
    """True when a `create_booking` failure means the slot can't be satisfied as
    requested (4xx / unknown) → propose alternatives. A 5xx is a transient server
    error → caller falls back gracefully instead of suggesting (likely-failing)
    slots. Status is carried in `IntegrationError.context['status']`."""
    status = e.context.get("status")
    return not isinstance(status, int) or status < 500


def _resolve_tz(tz_name: str) -> tzinfo:
    """Merchant's local timezone, falling back to UTC for an unknown name."""
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _parse_iso(s: str | None, tz: tzinfo = UTC) -> datetime | None:
    """Parse an ISO datetime. A naïve value (no offset — what the LLM usually
    emits) is interpreted in the merchant's local timezone `tz`, NOT UTC: the
    lead means "15:00" their time, so booking it as 15:00 UTC would land the
    appointment hours off. Values that already carry an offset are respected.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def _next_business_hour(
    tz: tzinfo = UTC,
    *,
    business_hours: list[Any] | None = None,
) -> datetime:
    """Fallback: next full hour within the merchant's business hours.

    When `business_hours` (list of BusinessHour ORM rows) is provided, the
    function walks forward day-by-day until it finds an open slot respecting
    open_time / close_time / break windows. Falls back to hardcoded 09:00-18:00
    when no rows are configured.
    """
    now = datetime.now(tz=tz)
    candidate = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    if not business_hours:
        # Legacy fallback: 09:00-18:00 any day
        if candidate.hour < 9:
            candidate = candidate.replace(hour=9)
        elif candidate.hour >= 18:
            candidate = (candidate + timedelta(days=1)).replace(hour=9)
        return candidate

    # Build day-of-week → row map (0=Mon … 6=Sun, same as Python weekday())
    bh_map: dict[int, Any] = {row.day_of_week: row for row in business_hours}

    for _ in range(14):  # max 2-week lookahead
        our_day = candidate.weekday()  # Python weekday: 0=Mon…6=Sun matches our convention
        row = bh_map.get(our_day)
        if row is None or not row.is_open or row.open_time is None or row.close_time is None:
            candidate = (candidate + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            continue

        open_h = row.open_time.hour
        close_h = row.close_time.hour

        # Snap to opening time if we're too early
        if candidate.hour < open_h:
            candidate = candidate.replace(hour=open_h, minute=0, second=0, microsecond=0)

        # Skip break window
        if row.break_start and row.break_end:
            bs_h = row.break_start.hour
            be_h = row.break_end.hour
            if bs_h <= candidate.hour < be_h:
                candidate = candidate.replace(hour=be_h, minute=0, second=0, microsecond=0)

        if candidate.hour >= close_h:
            candidate = (candidate + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            continue

        return candidate

    # Absolute fallback after 14 days
    return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)


def _format_human(iso: str) -> str:
    dt = _parse_iso(iso)
    if dt is None:
        return iso
    return dt.strftime("%d/%m alle %H:%M")


def format_booking_confirmation(
    *,
    booked: bool,
    slot_start_iso: str | None,
    suggested: list[str],
    local_only: bool = False,
) -> str:
    """Italian WhatsApp confirmation text for a booking outcome (pure).

    Reused by the live handler's `_send_confirmation` and by the UC-08 playground
    simulator, so the dry-run shows the exact message a real booking would send.
    """
    if booked and slot_start_iso:
        return (
            f"Perfetto, ho prenotato per te l'appuntamento del "
            f"{_format_human(slot_start_iso)}. Ti invieremo il promemoria."
        )
    if suggested:
        options = "\n".join(f"• {_format_human(s)}" for s in suggested)
        return (
            "Quello slot non è più disponibile. Ti suggerisco:\n"
            f"{options}\nFammi sapere quale preferisci."
        )
    return "Al momento non riesco a completare la prenotazione. Ti ricontatteremo a brevissimo."
