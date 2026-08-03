"""UC — escalate_human action handler.

The orchestrator emits `escalate_human` when the lead is angry, threatens a
complaint / legal action, or explicitly asks to talk to a person. This handler:
  1. Respects the merchant's `escalation.enabled` config — the agency can lock
     escalation off, in which case we leave the thread on the bot.
  2. Takes the bot off the thread (`conversation.auto_reply = False`) so the
     human agent owns the conversation from here on — via the atomic
     `claim_handoff`, unless the caller already won the claim for this turn
     (`turn_ctx.handoff_claimed`, set by the inbound reply policy which must
     claim *before* it sends).
  3. Emits a `conversation.escalated` analytics event — the merchant inbox
     surfaces it via Realtime and the automation engine turns it into the
     `conversation_escalated` trigger (Slack alerts, ADR 0020).

The user-facing handoff line is produced by the orchestrator's `reply_text`
(already sent before this handler runs); this handler only flips state and
notifies, it does not send another WhatsApp message.

Whoever loses the claim returns without emitting: exactly one episode, one
operator notification (ADR 0017).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_core.orchestrator import OrchestratorAction
from config_resolver import ConfigKey, ConfigResolver
from db import (
    AnalyticsRepository,
    ConversationRepository,
    TenantContext,
    tenant_session,
)
from shared import get_logger

if TYPE_CHECKING:
    from ai_core.conversation_service import TurnContext

logger = get_logger(__name__)


class EscalateHumanHandler:
    kind = "escalate_human"

    async def __call__(self, action: OrchestratorAction, turn_ctx: TurnContext) -> None:
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
            #
            # `handoff_claimed` overrides it: the caller already took the thread
            # off the bot. That happens on a hard LLM failure, where the reply
            # policy claims even with escalation locked off, as a safety net. If
            # we returned here the thread would be silent forever, the customer
            # would be holding a message promising an operator, and nobody would
            # have been told — the disabled switch would suppress the alert for a
            # takeover it did not prevent.
            if enabled is False and not turn_ctx.handoff_claimed:
                logger.info(
                    "escalate_human.disabled",
                    merchant_id=str(turn_ctx.merchant_id),
                    conversation_id=str(turn_ctx.conversation_id),
                )
                return

            reason = action.payload.get("reason")
            summary = action.payload.get("customer_message_summary")
            convs = ConversationRepository(session)
            # No reply policy ran ahead of us (proactive `ai_reply` node): take the
            # claim here. Losing means another turn already handed this thread off
            # — recording a second episode would reset `handoff_at`, re-arm the SLA
            # and fire a duplicate operator notification.
            if not turn_ctx.handoff_claimed and not await convs.claim_handoff(
                turn_ctx.conversation_id, reason=reason, summary=summary
            ):
                logger.info(
                    "escalate_human.already_handed_off",
                    merchant_id=str(turn_ctx.merchant_id),
                    conversation_id=str(turn_ctx.conversation_id),
                )
                return

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
                    "summary": summary,
                    "conversation_id": str(turn_ctx.conversation_id),
                },
            )

        logger.info(
            "escalate_human.done",
            merchant_id=str(turn_ctx.merchant_id),
            conversation_id=str(turn_ctx.conversation_id),
            reason=action.payload.get("reason"),
        )
