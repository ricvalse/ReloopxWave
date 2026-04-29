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

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from ai_core import ConversationService
from db import session_scope
from db.models.conversation import Conversation, Message
from db.repositories.integration import IntegrationRepository
from integrations.whatsapp.factory import build_whatsapp_sender
from shared import IntegrationError, get_logger, get_settings
from workers.runtime import Runtime

logger = get_logger(__name__)


async def handle_inbound_message(
    ctx: dict,
    phone_number_id: str,
    from_phone: str,
    text: str,
    wa_message_id: str,
) -> dict:
    runtime: Runtime = ctx["runtime"]
    service: ConversationService = runtime.conversation_service

    result = await service.handle_inbound(
        phone_number_id=phone_number_id,
        from_phone=from_phone,
        text=text,
        wa_message_id=wa_message_id,
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


async def handle_ghl_event(
    ctx: dict,
    merchant_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict:
    """Fan-out for GHL inbound webhooks (opportunity updates, bookings, contact
    changes). V1 logs + records; richer event routing (e.g. `OpportunityStatusUpdate`
    → analytics event) lands alongside UC-02/UC-04 completion.
    """
    logger.info(
        "ghl.event.received",
        merchant_id=merchant_id,
        event_type=event_type,
        keys=sorted(payload.keys()),
    )
    return {"merchant_id": merchant_id, "event_type": event_type}


async def send_outbound_whatsapp(ctx: dict, message_id: str) -> dict:
    """Dispatch a queued composer message to 360dialog.

    Service-role session: the lookup needs message + conversation + integration
    rows together, and the integration sits at the merchant level which we don't
    know until we've read the conversation. Once the row's resolved we issue
    the HTTP send and write back the terminal status atomically.

    Failure modes:
      - Conversation/message gone: log + return; no row to update.
      - Integration missing: row -> 'failed' with `error.code='no_integration'`.
      - D360 raises (already retried 3× by the client): row -> 'failed' with
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

    # HTTP call OUTSIDE the session — long external IO shouldn't hold a row lock.
    try:
        sender = build_whatsapp_sender(phone_number_id=phone_number_id, api_key=api_key)
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
    except Exception as exc:  # noqa: BLE001 — unknown errors are still terminal
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
    ctx: dict,
    wa_message_id: str,
    new_status: str,
    timestamp_unix: int | None,
    raw: dict[str, Any],
) -> dict:
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

    when = (
        datetime.fromtimestamp(timestamp_unix, tz=UTC)
        if timestamp_unix
        else datetime.now(UTC)
    )

    async with session_scope() as session:
        msg = (
            await session.execute(
                select(Message).where(Message.wa_message_id == wa_message_id)
            )
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

        await session.execute(
            update(Message).where(Message.id == msg.id).values(**values)
        )

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
