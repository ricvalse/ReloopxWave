"""Webhook-driven handlers — WhatsApp inbound + GHL events.

Both are entered from the public /webhooks routes once the signature has
validated. Idempotency on the WA side is the `_job_id=wa:msg:{id}` we set
on enqueue; ARQ skips duplicates. GHL events don't carry a stable dedupe key
in V1, so the handler is expected to be idempotent over its side effects.
"""

from __future__ import annotations

from typing import Any

from ai_core import ConversationService
from shared import get_logger
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
