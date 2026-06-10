"""UC — escalate_human action handler.

The orchestrator emits `escalate_human` when the lead is angry, threatens a
complaint / legal action, or explicitly asks to talk to a person. This handler:
  1. Respects the merchant's `escalation.enabled` config — the agency can lock
     escalation off, in which case we leave the thread on the bot.
  2. Takes the bot off the thread (`conversation.auto_reply = False`) so the
     human agent owns the conversation from here on.
  3. Stamps escalation metadata on the conversation and emits a
     `conversation.escalated` analytics event — the merchant inbox surfaces it
     via Realtime.

The user-facing handoff line is produced by the orchestrator's `reply_text`
(already sent before this handler runs); this handler only flips state and
notifies, it does not send another WhatsApp message.
"""

from __future__ import annotations

from ai_core.orchestrator import OrchestratorAction
from config_resolver import ConfigKey, ConfigResolver
from db import (
    AnalyticsRepository,
    ConversationRepository,
    TenantContext,
    tenant_session,
)
from shared import get_logger

logger = get_logger(__name__)


class EscalateHumanHandler:
    kind = "escalate_human"

    async def __call__(self, action: OrchestratorAction, turn_ctx) -> None:
        worker_ctx = TenantContext(
            tenant_id=turn_ctx.tenant_id,
            merchant_id=turn_ctx.merchant_id,
            role="worker",
            actor_id=turn_ctx.merchant_id,
        )

        async with tenant_session(worker_ctx) as session:
            config = ConfigResolver(session)
            enabled = await config.resolve(
                ConfigKey.ESCALATION_ENABLED, merchant_id=turn_ctx.merchant_id
            )
            # Only skip when explicitly disabled — escalating is the safe default
            # (better to hand a hot/angry lead to a human than to miss it).
            if enabled is False:
                logger.info(
                    "escalate_human.disabled",
                    merchant_id=str(turn_ctx.merchant_id),
                    conversation_id=str(turn_ctx.conversation_id),
                )
                return

            reason = action.payload.get("reason")
            convs = ConversationRepository(session)
            await convs.mark_escalated(turn_ctx.conversation_id, reason=reason)

            analytics = AnalyticsRepository(session)
            await analytics.emit(
                tenant_id=turn_ctx.tenant_id,
                merchant_id=turn_ctx.merchant_id,
                event_type="conversation.escalated",
                subject_type="conversation",
                subject_id=turn_ctx.conversation_id,
                properties={
                    "lead_id": str(turn_ctx.lead_id),
                    "reason": reason,
                    "conversation_id": str(turn_ctx.conversation_id),
                },
            )

        logger.info(
            "escalate_human.done",
            merchant_id=str(turn_ctx.merchant_id),
            conversation_id=str(turn_ctx.conversation_id),
            reason=action.payload.get("reason"),
        )
