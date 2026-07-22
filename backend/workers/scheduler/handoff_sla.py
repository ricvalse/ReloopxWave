"""Handoff SLA sweep — emit `conversation.handoff_overdue` for stale handoffs.

A human handoff (`auto_reply` off, `handoff_at` set, `handoff_resolved_at` null)
that stays open past `handoff_sla_minutes` gets a `conversation.handoff_overdue`
analytics event, **once per handoff episode** (edge-triggered on `handoff_at`,
mirroring the no-answer emitter — ADR 0015). That event drives any automation
subscribed to the `conversation_handoff_overdue` trigger, e.g. a Slack alert.

The sweep sends nothing itself and, like `followup_no_answer`, only fires when a
merchant actually has an enabled automation listening — otherwise it leaves the
anchor untouched so the trigger fires the moment they enable one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from db import (
    AnalyticsRepository,
    AutomationRepository,
    ConversationRepository,
    HandoffOverdueCandidate,
    TenantContext,
    session_scope,
    tenant_session,
)
from shared import get_logger, get_settings

logger = get_logger(__name__)

# Fallback when the setting is unset — a handoff shouldn't sit unseen for long.
_DEFAULT_SLA_MINUTES = 15


async def handoff_sla_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    settings = ctx.get("settings") or get_settings()
    threshold = int(
        getattr(settings, "handoff_sla_minutes", _DEFAULT_SLA_MINUTES) or _DEFAULT_SLA_MINUTES
    )
    # Guard misconfig: a non-positive value would push the cutoff into the future
    # (`now - -5min`) and match every open handoff, firing premature alerts.
    if threshold <= 0:
        threshold = _DEFAULT_SLA_MINUTES
    candidates = await _scan(threshold)
    logger.info("handoff_sla.scan", count=len(candidates), threshold_min=threshold)

    emitted = 0
    for cand in candidates:
        if await _maybe_emit(cand):
            emitted += 1

    return {"candidates": len(candidates), "emitted": emitted}


async def _scan(min_overdue_minutes: int) -> list[HandoffOverdueCandidate]:
    async with session_scope() as session:
        return await ConversationRepository(session).list_overdue_handoffs(
            min_overdue_minutes=min_overdue_minutes
        )


async def _maybe_emit(cand: HandoffOverdueCandidate) -> bool:
    # Edge gate: fire once per handoff episode, keyed on handoff_at.
    if cand.sla_fired_for is not None and cand.sla_fired_for >= cand.handoff_at:
        return False

    tenant_ctx = TenantContext(
        tenant_id=cand.tenant_id,
        merchant_id=cand.merchant_id,
        role="worker",
        actor_id=cand.merchant_id,
    )
    async with tenant_session(tenant_ctx) as session:
        autos = await AutomationRepository(session).list_enabled_by_trigger(
            merchant_id=cand.merchant_id, trigger_type="conversation_handoff_overdue"
        )
        if not autos:
            # Nobody listening — don't emit and don't burn the anchor, so the
            # trigger fires the moment the merchant enables an overdue automation.
            return False

        await ConversationRepository(session).mark_handoff_sla_fired(
            cand.conversation_id, cand.handoff_at
        )
        now = datetime.now(tz=UTC)
        await AnalyticsRepository(session).emit(
            tenant_id=cand.tenant_id,
            merchant_id=cand.merchant_id,
            event_type="conversation.handoff_overdue",
            subject_type="conversation",
            subject_id=cand.conversation_id,
            properties={
                "lead_id": str(cand.lead_id) if cand.lead_id else None,
                "conversation_id": str(cand.conversation_id),
                "overdue_minutes": int((now - cand.handoff_at).total_seconds() / 60),
            },
        )
        logger.info("handoff_sla.emitted", conversation_id=str(cand.conversation_id))
    return True
