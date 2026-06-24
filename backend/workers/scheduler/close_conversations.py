"""UC-13 trigger — close idle conversations and enqueue objection extraction.

There is no explicit "conversation closed" event in the WhatsApp flow, so we
approximate it: a conversation with no activity for `IDLE_CLOSE_MINUTES` is
considered finished. On close we enqueue `objection_extraction` for it, which is
the automatic post-conversation extraction the spec calls for (§5.3, §6.5) —
previously the extractor only ran when a human hit the manual API endpoint.

The threshold sits well after the UC-03 follow-up window (default 2nd reminder
at 1440 min) so closing never cuts off a pending reminder sequence.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from ai_core.scoring import BEHAVIOURAL_SIGNALS, score_lead
from config_resolver import SYSTEM_DEFAULTS, ConfigKey
from db import (
    AnalyticsRepository,
    ConversationRepository,
    LeadRepository,
    TenantContext,
    session_scope,
    tenant_session,
)
from shared import get_logger

logger = get_logger(__name__)

# Fallback idle threshold (minutes) if the config default is somehow unset.
_IDLE_CLOSE_FALLBACK_MIN = 120


def _idle_close_minutes() -> int:
    """Idle threshold from config (system default for the cascade), not a magic
    constant. The sweep is tenant-agnostic so we read the system-level default;
    a merchant override only changes the per-merchant view, not this sweep."""
    raw = SYSTEM_DEFAULTS.get(ConfigKey.CONVERSATION_IDLE_CLOSE_MINUTES)
    return int(raw) if isinstance(raw, int) else _IDLE_CLOSE_FALLBACK_MIN


async def close_idle_conversations(ctx: dict[str, Any]) -> dict[str, Any]:
    min_idle = _idle_close_minutes()
    async with session_scope() as session:
        repo = ConversationRepository(session)
        closed_ids = await repo.close_idle_active(min_idle_minutes=min_idle)
        # UC-05 — a conversation closed on prolonged silence is an abandonment:
        # mark its lead `dropped_off` and rescore (skip escalated threads).
        drop_targets = await repo.dropped_off_targets(closed_ids)
    # Commit happened on context exit; now fan out extraction jobs.

    rescored = await _rescore_dropped_off(drop_targets)

    redis = ctx.get("redis")
    enqueued = 0
    if redis is not None:
        for cid in closed_ids:
            await redis.enqueue_job(
                "objection_extraction",
                str(cid),
                _job_id=f"obj:extract:{cid}",
            )
            enqueued += 1
    else:  # pragma: no cover — redis is always present in the ARQ worker ctx
        logger.warning("uc13.close_sweep.no_redis")

    logger.info("uc13.close_sweep", closed=len(closed_ids), enqueued=enqueued, rescored=rescored)
    return {"closed": len(closed_ids), "enqueued": enqueued, "rescored": rescored}


async def _rescore_dropped_off(targets: list[tuple[UUID, UUID, UUID]]) -> int:
    """Mark each abandoned lead `dropped_off` and recompute its score (UC-05).

    `dropped_off` is a *content* signal, so we OR it into the lead's accumulated
    content signals. But scoring over content signals alone would silently drop
    the lead's behavioural contributions (has_name, engaged, positive_sentiment,
    asked_for_booking, …), which the live `update_score` handler always includes
    — cratering the lead far past the intended -20 penalty. Behavioural signals
    aren't persisted (they're recomputed from live conversation state each turn),
    but the last score's `score_reasons` records the full truthy set, so we
    recover the behavioural ones from there and rescore over the same
    {accumulated content + behavioural} set the live handler uses. A drop-off
    then costs at most the dropped_off penalty, never the whole behavioural score.
    """
    rescored = 0
    for lead_id, merchant_id, tenant_id in targets:
        ctx = TenantContext(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            role="worker",
            actor_id=merchant_id,
        )
        try:
            async with tenant_session(ctx) as session:
                leads = LeadRepository(session)
                lead = await leads.get(lead_id)
                if lead is None:
                    continue
                previous_score = lead.score
                accumulated = await leads.merge_content_signals(lead_id, {"dropped_off": True})
                behavioural = {
                    r: True for r in (lead.score_reasons or []) if r in BEHAVIOURAL_SIGNALS
                }
                effective_signals = {**behavioural, **accumulated}
                scored = score_lead(effective_signals)
                await leads.update_score(lead_id, score=scored.score, reasons=scored.reason_codes)
                await AnalyticsRepository(session).emit(
                    tenant_id=tenant_id,
                    merchant_id=merchant_id,
                    event_type="lead_score_changed",
                    subject_type="lead",
                    subject_id=lead_id,
                    properties={
                        "previous_score": previous_score,
                        "new_score": scored.score,
                        "reason_codes": scored.reason_codes,
                        "trigger": "dropped_off",
                    },
                )
            rescored += 1
        except Exception as e:  # pragma: no cover — defensive per-lead isolation
            logger.warning("uc05.dropoff_rescore_failed", lead_id=str(lead_id), error=str(e))
    return rescored
