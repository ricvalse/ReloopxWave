"""UC-01 First Response handler.

Entry point for every WhatsApp inbound text. Idempotency is guaranteed
by the `_job_id=wa:msg:{id}` we set on enqueue — ARQ skips duplicates.
"""
from __future__ import annotations

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
