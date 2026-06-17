"""Webhook-driven handlers — WhatsApp inbound + GHL events + outbound send.

Inbound:
  WA messages arrive via /webhooks → enqueue → `handle_inbound_message` → ConversationService.
  Idempotency on the WA side is the `_job_id=wa:msg:{id}` we set on enqueue;
  ARQ skips duplicates. GHL events don't carry a stable dedupe key in V1, so
  the handler is expected to be idempotent over its side effects.

Outbound (composer):
  Frontend POSTs `/conversations/{id}/messages` → FastAPI inserts row with
  `status='pending'` under RLS → enqueues `send_outbound_whatsapp` here.
  The worker re-fetches under a service-role session (lookups by id need to
  cross merchant scope to find the integration before applying tenancy).
  Terminal states only: status='sent' (with wa_message_id) or 'failed'
  (with error). Webhook callbacks turn 'sent' into 'delivered'/'read'.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, update

from ai_core import ConversationService, RescheduleBy, debounce_decision
from db import (
    ConversationRepository,
    GHLMarketplaceRepository,
    LeadRepository,
    ResolvedAgencyInstall,
    session_scope,
)
from db.models.conversation import Conversation, Message
from db.repositories.integration import IntegrationRepository
from integrations import (
    GHLClient,
    GHLTokenBundle,
    MintedLocationToken,
    mint_location_token,
)
from integrations.whatsapp.factory import build_whatsapp_sender
from shared import IntegrationError, get_logger, get_settings
from workers.runtime import Runtime

logger = get_logger(__name__)


def _debounce_keys(merchant_id: str, from_phone: str) -> tuple[str, str, str]:
    """Buffer list key, due-epoch key, and the stable per-peer flush job id."""
    buf = f"debounce:wa:buf:{merchant_id}:{from_phone}"
    due = f"debounce:wa:due:{merchant_id}:{from_phone}"
    job = f"wa:flush:{merchant_id}:{from_phone}"
    return buf, due, job


def _to_epoch(raw: Any) -> float:
    if raw is None:
        return 0.0
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


async def handle_inbound_message(
    ctx: dict[str, Any],
    phone_number_id: str,
    from_phone: str,
    text: str,
    wa_message_id: str,
) -> dict[str, Any]:
    runtime: Runtime = ctx["runtime"]
    service: ConversationService = runtime.conversation_service

    # Phase 1: always persist the inbound durably and read the auto-reply gate +
    # the per-merchant debounce window. The reply itself is gated below.
    outcome = await service.handle_inbound_persist(
        phone_number_id=phone_number_id,
        from_phone=from_phone,
        text=text,
        wa_message_id=wa_message_id,
    )

    if not (outcome.handled and outcome.auto_reply_on):
        logger.info(
            "uc01.handled",
            phone_number_id=phone_number_id,
            handled=outcome.handled,
            reason=outcome.reason,
            conversation_id=str(outcome.conversation_id) if outcome.conversation_id else None,
        )
        return {
            "handled": outcome.handled,
            "reason": outcome.reason,
            "conversation_id": str(outcome.conversation_id) if outcome.conversation_id else None,
        }

    config_redis = ctx.get("config_redis")
    arq = ctx.get("redis")
    merchant_id = str(outcome.merchant_id)

    # Debounce: coalesce rapid messages from the same peer into one reply. Needs
    # both Redis handles; without them we degrade to an immediate reply so a
    # transient Redis blip never drops the response.
    if outcome.debounce_window_s > 0 and config_redis is not None and arq is not None:
        window = outcome.debounce_window_s
        buf_key, due_key, job_id = _debounce_keys(merchant_id, from_phone)
        entry = json.dumps({"wa_message_id": wa_message_id, "text": text})
        due = time.time() + window
        async with config_redis.pipeline(transaction=True) as pipe:
            await (
                pipe.rpush(buf_key, entry)
                .expire(buf_key, window + 60)
                .set(due_key, due, ex=window + 60)
                .execute()
            )
        # Stable job id → a pending flush is reused; the flush self-reschedules
        # off the due epoch above until the peer goes quiet for `window`.
        await arq.enqueue_job(
            "flush_inbound_reply",
            merchant_id,
            from_phone,
            phone_number_id,
            _job_id=job_id,
            _defer_by=timedelta(seconds=window),
        )
        logger.info(
            "uc01.debounced",
            phone_number_id=phone_number_id,
            conversation_id=str(outcome.conversation_id),
            window_s=window,
        )
        return {
            "handled": True,
            "reason": "debounced",
            "conversation_id": str(outcome.conversation_id),
        }

    # No debounce: reply now. Re-resolves fresh context and excludes this inbound
    # from the LLM history (it's already persisted).
    result = await service.generate_and_send_reply(
        phone_number_id=phone_number_id,
        from_phone=from_phone,
        text=text,
        wa_message_id=wa_message_id,
        exclude_wa_message_ids=[wa_message_id] if wa_message_id else [],
    )
    logger.info(
        "uc01.handled",
        phone_number_id=phone_number_id,
        handled=result.handled,
        reason=result.reason,
        conversation_id=str(result.conversation_id) if result.conversation_id else None,
    )
    return {
        "handled": result.handled,
        "reason": result.reason,
        "conversation_id": str(result.conversation_id) if result.conversation_id else None,
    }


async def flush_inbound_reply(
    ctx: dict[str, Any],
    merchant_id: str,
    from_phone: str,
    phone_number_id: str,
) -> dict[str, Any]:
    """Debounce flush: once a peer has been quiet for the window, drain the
    buffered inbound fragments and generate ONE reply covering them all.

    Self-reschedules while a newer inbound keeps pushing the due epoch out, so
    the reply always lands after the LAST message. Idempotent: the buffer is
    drained-and-deleted, so a re-run finds it empty and no-ops (no double reply).
    """
    runtime: Runtime = ctx["runtime"]
    service: ConversationService = runtime.conversation_service
    config_redis = ctx.get("config_redis")
    arq = ctx.get("redis")
    if config_redis is None:  # pragma: no cover — redis is always present in ctx
        logger.warning("uc01.flush.no_redis", merchant_id=merchant_id)
        return {"flushed": False, "reason": "no_redis"}

    buf_key, due_key, job_id = _debounce_keys(merchant_id, from_phone)

    # 1. Reschedule if a newer message pushed the deadline into the future.
    due = _to_epoch(await config_redis.get(due_key))
    decision = debounce_decision(time.time(), due)
    if isinstance(decision, RescheduleBy):
        if arq is not None:
            await arq.enqueue_job(
                "flush_inbound_reply",
                merchant_id,
                from_phone,
                phone_number_id,
                _job_id=job_id,
                _defer_by=timedelta(seconds=max(decision.seconds, 0.1)),
            )
        return {"flushed": False, "reason": "rescheduled"}

    # 2. Drain the buffer atomically (read-all + delete) so a concurrent flush or
    #    re-run can't reply twice.
    async with config_redis.pipeline(transaction=True) as pipe:
        results = await pipe.lrange(buf_key, 0, -1).delete(buf_key).delete(due_key).execute()
    entries_raw = results[0] or []
    if not entries_raw:
        return {"flushed": False, "reason": "empty"}

    entries = [json.loads(e) for e in entries_raw]
    texts = [e["text"] for e in entries if e.get("text")]
    wa_ids = [e["wa_message_id"] for e in entries if e.get("wa_message_id")]
    if not texts:
        return {"flushed": False, "reason": "empty"}

    result = await service.generate_and_send_reply(
        phone_number_id=phone_number_id,
        from_phone=from_phone,
        text="\n".join(texts),
        wa_message_id=wa_ids[-1] if wa_ids else None,
        exclude_wa_message_ids=wa_ids,
    )
    logger.info(
        "uc01.flushed",
        phone_number_id=phone_number_id,
        merchant_id=merchant_id,
        messages=len(texts),
        handled=result.handled,
        conversation_id=str(result.conversation_id) if result.conversation_id else None,
    )
    return {
        "flushed": result.handled,
        "messages": len(texts),
        "conversation_id": str(result.conversation_id) if result.conversation_id else None,
    }


async def handle_phone_app_echo(
    ctx: dict[str, Any],
    phone_number_id: str,
    customer_phone: str,
    text: str,
    wa_message_id: str,
) -> dict[str, Any]:
    """Mirror a phone-app-typed message into the conversations DB.

    Only emitted by 360dialog Coexistence channels. The orchestrator is
    deliberately skipped — the customer already saw the reply on WhatsApp.
    """
    runtime: Runtime = ctx["runtime"]
    service: ConversationService = runtime.conversation_service

    result = await service.handle_phone_app_echo(
        phone_number_id=phone_number_id,
        customer_phone=customer_phone,
        text=text,
        wa_message_id=wa_message_id,
    )
    logger.info(
        "wa.phone_echo.handled",
        phone_number_id=phone_number_id,
        wa_message_id=wa_message_id,
        handled=result.handled,
        reason=result.reason,
        conversation_id=str(result.conversation_id) if result.conversation_id else None,
    )
    return {
        "handled": result.handled,
        "reason": result.reason,
        "conversation_id": str(result.conversation_id) if result.conversation_id else None,
    }


# Failed-call outcomes that should trigger the AI WhatsApp takeover (UC-03). GHL
# delivers call results via a workflow webhook whose payload the agency shapes,
# so we match defensively on normalised tokens rather than one canonical value.
_CALL_FAILED_TOKENS = (
    "no_answer",
    "noanswer",
    "not_answered",
    "unanswered",
    "busy",
    "failed",
    "no_show",
    "voicemail",
)
_CALL_STATUS_KEYS = ("callStatus", "call_status", "callOutcome", "call_outcome")


def _opt_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _ghl_phone(payload: dict[str, Any]) -> str | None:
    for key in ("phone", "contactPhone", "contact_phone"):
        if payload.get(key):
            return str(payload[key])
    contact = payload.get("contact")
    if isinstance(contact, dict) and contact.get("phone"):
        return str(contact["phone"])
    return None


def _ghl_full_name(payload: dict[str, Any]) -> str | None:
    name = payload.get("name") or payload.get("full_name")
    if name:
        return str(name)
    first = payload.get("firstName") or payload.get("first_name")
    last = payload.get("lastName") or payload.get("last_name")
    parts = [str(p) for p in (first, last) if p]
    return " ".join(parts) or None


def _detect_call_outcome(event_type_lc: str, payload: dict[str, Any]) -> str | None:
    """Return a normalised call-outcome token if this event looks like a call
    result, else None."""
    has_call_key = any(payload.get(k) for k in _CALL_STATUS_KEYS)
    if "call" not in event_type_lc and not has_call_key:
        return None
    raw = next((payload[k] for k in _CALL_STATUS_KEYS if payload.get(k)), None)
    raw = raw or payload.get("status") or payload.get("outcome")
    if not raw and "call" in event_type_lc:
        raw = event_type_lc  # e.g. the type itself encodes "OutboundCallNoAnswer"
    if not raw:
        return None
    return str(raw).strip().lower().replace(" ", "_").replace("-", "_")


async def _resolve_lead(
    leads: LeadRepository, merchant_id: UUID, contact_id: str | None, phone: str | None
) -> Any:
    lead = None
    if contact_id:
        lead = await leads.get_by_ghl_contact_id(merchant_id=merchant_id, ghl_contact_id=contact_id)
    if lead is None and phone:
        lead = await leads.get_by_phone(merchant_id=merchant_id, phone=phone)
    return lead


async def handle_ghl_event(
    ctx: dict[str, Any],
    merchant_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Route a GHL data webhook to a lead-state sync (UC-01/02/04) or, when it
    carries a failed-call result, to the WhatsApp takeover (UC-03).

    Runs on a service-role session (the signed webhook is trusted; every query
    is explicitly scoped by `merchant_id`). Logged with a system actor marker.
    """
    et = event_type.lower()

    outcome = _detect_call_outcome(et, payload)
    if outcome is not None:
        return await handle_call_outcome(
            ctx,
            merchant_id=merchant_id,
            contact_phone=_ghl_phone(payload) or "",
            outcome=outcome,
            ghl_contact_id=_opt_str(payload.get("contactId") or payload.get("contact_id")),
        )

    mid = UUID(merchant_id)
    matched = False
    async with session_scope() as session:
        leads = LeadRepository(session)
        if "contact" in et:
            contact_id = _opt_str(
                payload.get("id") or payload.get("contactId") or payload.get("contact_id")
            )
            lead = await _resolve_lead(leads, mid, contact_id, _ghl_phone(payload))
            if lead is not None:
                matched = True
                await leads.update_contact_fields(
                    lead.id, name=_ghl_full_name(payload), email=_opt_str(payload.get("email"))
                )
                if contact_id and not lead.ghl_contact_id:
                    lead.ghl_contact_id = contact_id
        elif "opportunity" in et:
            contact_id = _opt_str(payload.get("contactId") or payload.get("contact_id"))
            lead = await _resolve_lead(leads, mid, contact_id, _ghl_phone(payload))
            if lead is not None:
                matched = True
                stage = _opt_str(
                    payload.get("pipelineStageId")
                    or payload.get("stageId")
                    or payload.get("pipeline_stage_id")
                )
                if stage:
                    await leads.set_pipeline_stage(lead.id, stage_id=stage)
                opp_id = _opt_str(payload.get("id") or payload.get("opportunityId"))
                if opp_id:
                    lead.meta = {**(lead.meta or {}), "ghl_opportunity_id": opp_id}
        # Appointment events carry no durable lead field to sync in V1; reminder
        # cancellation handling lands with the UC-02 appointment reminders.

    logger.info(
        "ghl.event.routed",
        merchant_id=merchant_id,
        event_type=event_type,
        matched=matched,
        actor="system:ghl_webhook",
    )
    return {"merchant_id": merchant_id, "event_type": event_type, "matched": matched}


async def handle_call_outcome(
    ctx: dict[str, Any],
    *,
    merchant_id: str,
    contact_phone: str,
    outcome: str,
    ghl_contact_id: str | None = None,
) -> dict[str, Any]:
    """UC-03 — a phone call to the lead failed; take control on WhatsApp.

    Ensures an active conversation for the lead exists, marks it as originating
    from a failed call, and stamps it so the no-answer follow-up flow
    (`followup_no_answer`) picks it up. The first outreach itself goes through
    `workers.outbound.decide_outbound`, which respects the 24h window and only
    sends outside it once an approved template/flow step is configured (CC-WA) —
    until then it skips cleanly rather than failing.
    """
    norm = str(outcome).strip().lower().replace(" ", "_").replace("-", "_")
    actionable = norm in _CALL_FAILED_TOKENS or any(tok in norm for tok in _CALL_FAILED_TOKENS)
    if not actionable:
        return {"handled": False, "reason": "outcome_not_actionable", "outcome": norm}
    if not contact_phone:
        return {"handled": False, "reason": "no_contact_phone"}

    settings = get_settings()
    mid = UUID(merchant_id)
    async with session_scope() as session:
        leads = LeadRepository(session)
        convs = ConversationRepository(session)
        integrations = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)

        lead = None
        if ghl_contact_id:
            lead = await leads.get_by_ghl_contact_id(merchant_id=mid, ghl_contact_id=ghl_contact_id)
        if lead is None:
            lead = await leads.get_by_phone(merchant_id=mid, phone=contact_phone)

        conv = await convs.get_active(merchant_id=mid, wa_contact_phone=contact_phone)
        if conv is None:
            wa = await integrations.resolve_whatsapp_by_merchant(mid)
            if wa is None or not wa.phone_number_id:
                logger.warning("ghl.call_outcome.no_wa_channel", merchant_id=merchant_id)
                return {"handled": False, "reason": "no_whatsapp_channel"}
            conv = await convs.create(
                merchant_id=mid,
                lead_id=lead.id if lead is not None else None,
                wa_phone_number_id=wa.phone_number_id,
                wa_contact_phone=contact_phone,
            )

        conv.meta = {**(conv.meta or {}), "origin": "call_failed", "call_outcome": norm}
        await convs.touch_last_message(conv.id)
        conv_id = conv.id

    logger.info(
        "ghl.call_outcome.primed",
        merchant_id=merchant_id,
        outcome=norm,
        conversation_id=str(conv_id),
        actor="system:ghl_webhook",
    )
    return {"handled": True, "outcome": norm, "conversation_id": str(conv_id)}


async def handle_ghl_install(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Marketplace INSTALL event — record the location and mint its token.

    Resolves the tenant from `companyId` (set when the agency connected from
    web-admin), records a `pending_link` location, mints a Location-level token
    from the agency token, and stores it. Idempotent on re-delivery.
    """
    settings = get_settings()
    location_id = str(payload.get("locationId") or payload.get("location_id") or "")
    company_id = str(payload.get("companyId") or payload.get("company_id") or "")
    user_id = payload.get("userId") or payload.get("user_id")
    if not location_id or not company_id:
        logger.warning("ghl.install.missing_ids", payload_keys=sorted(payload.keys()))
        return {"installed": False, "reason": "missing_ids"}

    async with session_scope() as session:
        repo = GHLMarketplaceRepository(session, kek_base64=settings.integrations_kek_base64)
        agency = await repo.resolve_agency_by_company_id(company_id)
        if agency is None:
            # INSTALL arrived before the agency connected from web-admin. Drop it
            # (200) rather than retry-storm; the agency-connect path is the
            # expected ordering, and a re-install after connect recovers cleanly.
            logger.warning("ghl.install.no_agency", company_id=company_id, location_id=location_id)
            return {"installed": False, "reason": "no_agency_install"}
        await repo.upsert_location_install(
            tenant_id=agency.tenant_id,
            company_id=company_id,
            location_id=location_id,
            installed_by_user_id=str(user_id) if user_id else None,
        )

    try:
        minted, agency = await _mint_location_token_with_retry(
            settings, agency, company_id, location_id
        )
    except IntegrationError as exc:
        logger.warning(
            "ghl.install.mint_failed", location_id=location_id, error_code=exc.error_code
        )
        return {"installed": False, "reason": "mint_failed", "location_id": location_id}

    location_name = await _fetch_location_name(settings, minted.access_token, location_id)

    async with session_scope() as session:
        repo = GHLMarketplaceRepository(session, kek_base64=settings.integrations_kek_base64)
        if location_name:
            await repo.upsert_location_install(
                tenant_id=agency.tenant_id,
                company_id=company_id,
                location_id=location_id,
                location_name=location_name,
            )
        await repo.set_location_token(
            location_id=location_id,
            access_token=minted.access_token,
            refresh_token=minted.refresh_token,
            expires_at=minted.expires_at,
        )

    logger.info(
        "ghl.install.completed",
        location_id=location_id,
        company_id=company_id,
        tenant_id=str(agency.tenant_id),
        named=bool(location_name),
    )
    return {"installed": True, "location_id": location_id}


async def handle_ghl_uninstall(ctx: dict[str, Any], location_id: str) -> dict[str, Any]:
    """Marketplace UNINSTALL event — revoke the location token (soft delete).

    Keeps the merchant link for audit/re-install; clears the dead token.
    Idempotent on re-delivery.
    """
    settings = get_settings()
    async with session_scope() as session:
        repo = GHLMarketplaceRepository(session, kek_base64=settings.integrations_kek_base64)
        revoked = await repo.revoke_location(str(location_id))
    logger.info("ghl.uninstall.handled", location_id=str(location_id), revoked=revoked)
    return {"revoked": revoked, "location_id": str(location_id)}


async def _mint_location_token_with_retry(
    settings: Any,
    agency: ResolvedAgencyInstall,
    company_id: str,
    location_id: str,
) -> tuple[MintedLocationToken, ResolvedAgencyInstall]:
    """Mint a location token; on a rejected (stale) agency token, refresh it once
    and retry. Returns the minted token and the (possibly refreshed) agency."""
    try:
        minted = await mint_location_token(
            agency_access_token=agency.access_token,
            company_id=company_id,
            location_id=location_id,
        )
        return minted, agency
    except IntegrationError as exc:
        if exc.error_code != "ghl_location_mint_rejected":
            raise
        agency = await _refresh_agency_token(settings, agency)
        minted = await mint_location_token(
            agency_access_token=agency.access_token,
            company_id=company_id,
            location_id=location_id,
        )
        return minted, agency


async def _refresh_agency_token(
    settings: Any, agency: ResolvedAgencyInstall
) -> ResolvedAgencyInstall:
    """Refresh + persist the agency (Company) token, return the fresh install."""
    client = GHLClient(
        token_bundle=GHLTokenBundle(
            access_token=agency.access_token,
            refresh_token=agency.refresh_token,
            expires_at=agency.expires_at,
            company_id=agency.company_id,
            user_type="Company",
        ),
        client_id=settings.ghl_client_id,
        client_secret=settings.ghl_client_secret,
    )
    try:
        new_bundle = await client.refresh_now()
    finally:
        await client.close()

    async with session_scope() as session:
        repo = GHLMarketplaceRepository(session, kek_base64=settings.integrations_kek_base64)
        await repo.upsert_agency_install(
            tenant_id=agency.tenant_id,
            company_id=agency.company_id,
            access_token=new_bundle.access_token,
            refresh_token=new_bundle.refresh_token,
            expires_at=new_bundle.expires_at,
            company_name=agency.company_name,
        )
    return ResolvedAgencyInstall(
        tenant_id=agency.tenant_id,
        company_id=agency.company_id,
        access_token=new_bundle.access_token,
        refresh_token=new_bundle.refresh_token,
        expires_at=new_bundle.expires_at,
        status="active",
        company_name=agency.company_name,
    )


async def _fetch_location_name(
    settings: Any, location_access_token: str, location_id: str
) -> str | None:
    """Best-effort sub-account name for the linking UI. Never raises."""
    client = GHLClient(
        token_bundle=GHLTokenBundle(
            access_token=location_access_token,
            refresh_token="",
            expires_at=0,
            location_id=location_id,
            user_type="Location",
        ),
        client_id=settings.ghl_client_id,
        client_secret=settings.ghl_client_secret,
    )
    try:
        resp = await client.get_location(location_id)
    except Exception as exc:  # pragma: no cover — name is cosmetic, never block
        logger.info("ghl.install.location_name_failed", location_id=location_id, error=str(exc))
        return None
    finally:
        await client.close()
    raw_loc = resp.get("location")
    loc = raw_loc if isinstance(raw_loc, dict) else resp
    name = loc.get("name") if isinstance(loc, dict) else None
    return str(name) if name else None


async def send_outbound_whatsapp(ctx: dict[str, Any], message_id: str) -> dict[str, Any]:
    """Dispatch a queued composer message to 360dialog.

    Service-role session: the lookup needs message + conversation + integration
    rows together, and the integration sits at the merchant level which we don't
    know until we've read the conversation. Once the row's resolved we issue
    the HTTP send and write back the terminal status atomically.

    Failure modes:
      - Conversation/message gone: log + return; no row to update.
      - Integration missing: row -> 'failed' with `error.code='no_integration'`.
      - D360 raises (already retried 3x by the client): row -> 'failed' with
        the underlying error_code and status. Never leave a row 'pending'.
    """
    settings = get_settings()
    async with session_scope() as session:
        msg = (
            await session.execute(select(Message).where(Message.id == message_id))
        ).scalar_one_or_none()
        if msg is None:
            logger.warning("wa.outbound.message_missing", message_id=message_id)
            return {"sent": False, "reason": "message_missing"}

        if msg.status not in ("pending", "failed"):
            # Idempotent: a re-enqueue (e.g. ARQ retry) shouldn't re-send a
            # message that already reached a sent/delivered/read terminal.
            logger.info(
                "wa.outbound.skip_non_pending",
                message_id=message_id,
                status=msg.status,
            )
            return {"sent": False, "reason": "already_terminal", "status": msg.status}

        conv = (
            await session.execute(
                select(Conversation).where(Conversation.id == msg.conversation_id)
            )
        ).scalar_one_or_none()
        if conv is None:
            await _mark_failed(
                session,
                message_id=message_id,
                error_code="conversation_missing",
                detail="conversation gone",
            )
            return {"sent": False, "reason": "conversation_missing"}

        if not conv.wa_phone_number_id or not conv.wa_contact_phone:
            await _mark_failed(
                session,
                message_id=message_id,
                error_code="conversation_missing_routing",
                detail="conversation has no wa_phone_number_id or wa_contact_phone",
            )
            return {"sent": False, "reason": "missing_routing"}

        repo = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)
        resolved = await repo.resolve_whatsapp(conv.wa_phone_number_id)
        if resolved is None:
            await _mark_failed(
                session,
                message_id=message_id,
                error_code="no_integration",
                detail="no active WhatsApp integration for phone_number_id",
            )
            return {"sent": False, "reason": "no_integration"}

        text_to_send = msg.content
        to_phone = conv.wa_contact_phone
        phone_number_id = resolved.phone_number_id
        api_key = resolved.api_key
        waba_base_url = resolved.waba_base_url

    # HTTP call OUTSIDE the session — long external IO shouldn't hold a row lock.
    try:
        sender = build_whatsapp_sender(
            phone_number_id=phone_number_id,
            api_key=api_key,
            waba_base_url=waba_base_url,
        )
        try:
            resp = await sender.send_text(to_phone=to_phone, text=text_to_send)
        finally:
            await sender.close()
    except IntegrationError as exc:
        async with session_scope() as session:
            await _mark_failed(
                session,
                message_id=message_id,
                error_code=exc.error_code or "send_failed",
                detail=str(exc),
                extra={"status": getattr(exc, "status", None)},
            )
        logger.warning(
            "wa.outbound.failed",
            message_id=message_id,
            error_code=exc.error_code,
            status=getattr(exc, "status", None),
        )
        return {"sent": False, "reason": "send_failed", "error_code": exc.error_code}
    except Exception as exc:
        async with session_scope() as session:
            await _mark_failed(
                session,
                message_id=message_id,
                error_code="unexpected_error",
                detail=str(exc),
            )
        logger.exception("wa.outbound.unexpected", message_id=message_id)
        return {"sent": False, "reason": "unexpected"}

    wa_id = str((resp.get("messages") or [{}])[0].get("id", "") or "") or None

    async with session_scope() as session:
        await session.execute(
            update(Message)
            .where(Message.id == message_id)
            .values(
                status="sent",
                wa_message_id=wa_id,
                error=None,
            )
        )

    logger.info(
        "wa.outbound.sent",
        message_id=message_id,
        wa_message_id=wa_id,
    )
    return {"sent": True, "wa_message_id": wa_id}


async def update_outbound_status(
    ctx: dict[str, Any],
    wa_message_id: str,
    new_status: str,
    timestamp_unix: int | None,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Apply a Meta/D360 status callback to the outbound row.

    Status state machine (monotonic — never go backwards):
      pending -> sent -> delivered -> read    (happy path)
              -> failed                       (terminal error, never overwritten)

    Late-arriving lower-tier callbacks (e.g. 'delivered' arriving after 'read')
    are dropped to keep the user-visible tick state stable.
    """
    if not wa_message_id:
        logger.warning("wa.status.missing_id", raw=raw)
        return {"updated": False, "reason": "missing_wa_message_id"}

    new_status = new_status.lower()
    if new_status not in {"sent", "delivered", "read", "failed"}:
        logger.info("wa.status.unknown", status=new_status, wa_message_id=wa_message_id)
        return {"updated": False, "reason": "unknown_status"}

    when = datetime.fromtimestamp(timestamp_unix, tz=UTC) if timestamp_unix else datetime.now(UTC)

    async with session_scope() as session:
        msg = (
            await session.execute(select(Message).where(Message.wa_message_id == wa_message_id))
        ).scalar_one_or_none()
        if msg is None:
            logger.info("wa.status.row_missing", wa_message_id=wa_message_id)
            return {"updated": False, "reason": "row_missing"}

        if msg.status == "failed":
            return {"updated": False, "reason": "already_failed"}

        rank = {"pending": 0, "sent": 1, "delivered": 2, "read": 3, "failed": 99}
        current_rank = rank.get(msg.status, 0)
        new_rank = rank.get(new_status, 0)
        if new_status != "failed" and new_rank <= current_rank:
            return {"updated": False, "reason": "stale_status", "current": msg.status}

        values: dict[str, Any] = {"status": new_status}
        if new_status == "delivered" and msg.delivered_at is None:
            values["delivered_at"] = when
        elif new_status == "read":
            if msg.delivered_at is None:
                values["delivered_at"] = when
            values["read_at"] = when
        elif new_status == "failed":
            values["failed_at"] = when
            errors = raw.get("errors") or []
            if errors:
                values["error"] = {
                    "code": "wa_status_failed",
                    "detail": errors[0].get("title") or errors[0].get("message") or "",
                    "raw": errors[0],
                }

        await session.execute(update(Message).where(Message.id == msg.id).values(**values))

    logger.info(
        "wa.status.updated",
        wa_message_id=wa_message_id,
        status=new_status,
        message_id=str(msg.id),
    )
    return {"updated": True, "status": new_status, "message_id": str(msg.id)}


async def _mark_failed(
    session: Any,
    *,
    message_id: str,
    error_code: str,
    detail: str,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {"code": error_code, "detail": detail}
    if extra:
        payload.update({k: v for k, v in extra.items() if v is not None})
    await session.execute(
        update(Message)
        .where(Message.id == message_id)
        .values(
            status="failed",
            failed_at=datetime.now(UTC),
            error=payload,
        )
    )
