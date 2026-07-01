"""UC-03 — no-answer trigger emitter.

ADR 0015: this scheduler is a pure *edge-triggered emitter*. Every 15 minutes it
scans for conversations that went silent, and emits a `lead.no_answer` analytics
event **once per silence episode**. It sends nothing itself — the response
(content + multi-attempt cadence) lives entirely in the merchant's automation on
the lavagnetta, dispatched by the automation engine off this event.

Idempotency is edge-triggered, not Redis-based: the emission is anchored on the
conversation's `last_inbound_at` (`no_answer_fired_for`). We fire once and
suppress until the lead sends a new inbound (advancing `last_inbound_at`), which
re-arms the trigger for the next silence episode.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from db import (
    AnalyticsRepository,
    AutomationRepository,
    ConversationRepository,
    ReminderCandidate,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.models.automation import AutomationFlow
from shared import get_logger

logger = get_logger(__name__)

# Conservative floor: a conversation must be silent at least this long before it
# can count as a "no answer". The merchant's real first-reminder delay is set on
# the trigger node (`delay_minutes`) and/or a leading `wait` in the graph.
_MIN_IDLE_MINUTES = 30


async def followup_no_answer(ctx: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    candidates = await _scan_candidates()
    logger.info("uc03.scan", count=len(candidates))

    emitted = 0
    for cand in candidates:
        if await _maybe_emit(cand, now=now):
            emitted += 1

    return {"candidates": len(candidates), "emitted": emitted}


async def _scan_candidates() -> list[ReminderCandidate]:
    async with session_scope() as session:
        repo = ConversationRepository(session)
        return await repo.list_reminder_candidates(min_idle_minutes=_MIN_IDLE_MINUTES)


async def _maybe_emit(cand: ReminderCandidate, *, now: datetime) -> bool:
    # Edge gate: fire once per silence episode, keyed on last_inbound_at.
    if cand.last_inbound_at is None:
        return False
    if cand.no_answer_fired_for is not None and cand.no_answer_fired_for >= cand.last_inbound_at:
        return False

    tenant_ctx = TenantContext(
        tenant_id=cand.tenant_id,
        merchant_id=cand.merchant_id,
        role="worker",
        actor_id=cand.merchant_id,
    )
    async with tenant_session(tenant_ctx) as session:
        autos = await AutomationRepository(session).list_enabled_by_trigger(
            merchant_id=cand.merchant_id, trigger_type="no_answer"
        )
        if not autos:
            # Nobody is listening — don't emit (and don't burn the anchor, so the
            # trigger fires the moment the merchant enables a no-answer automation).
            return False

        threshold_min = _threshold_minutes(autos)
        if now - cand.last_message_at < timedelta(minutes=threshold_min):
            return False

        await ConversationRepository(session).mark_no_answer_fired(
            cand.conversation_id, cand.last_inbound_at
        )
        await AnalyticsRepository(session).emit(
            tenant_id=cand.tenant_id,
            merchant_id=cand.merchant_id,
            event_type="lead.no_answer",
            subject_type="conversation",
            subject_id=cand.conversation_id,
            properties={
                "idle_minutes": int((now - cand.last_message_at).total_seconds() / 60),
                # Re-engagement anchor: the engine cancels a stale cadence if the
                # lead's last_inbound_at advances past this at resume time.
                "episode_anchor": cand.last_inbound_at.isoformat(),
            },
        )
        logger.info("uc03.emitted", conversation_id=str(cand.conversation_id))
    return True


def _threshold_minutes(autos: list[AutomationFlow]) -> int:
    """Smallest `no_answer` trigger delay across the enabled automations — the
    trigger fires as soon as the earliest-configured one wants it. Falls back to
    the idle floor when no trigger sets an explicit `delay_minutes`."""
    values: list[int] = []
    for auto in autos:
        trigger = next((n for n in auto.nodes if n.kind == "trigger"), None)
        delay = (trigger.config or {}).get("delay_minutes") if trigger else None
        if isinstance(delay, (int, float)) and delay > 0:
            values.append(int(delay))
    return min(values) if values else _MIN_IDLE_MINUTES
