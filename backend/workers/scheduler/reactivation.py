"""UC-06 — dormant-lead trigger emitter.

ADR 0015: this scheduler is a pure *edge-triggered emitter*. Daily it scans for
leads whose most-recent conversation activity is older than the merchant's
configured dormancy threshold, and emits a `lead.dormant` analytics event **once
per dormancy episode**. It sends nothing — the reactivation message(s) and their
cadence live in the merchant's automation on the lavagnetta, dispatched by the
automation engine off this event.

Idempotency is edge-triggered: the emission is anchored on the lead's
`last_interaction_at` (`dormant_fired_for`). We fire once and re-arm only when the
lead re-engages (advancing their max conversation activity past the anchor).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from db import (
    AnalyticsRepository,
    AutomationRepository,
    LeadRepository,
    ReactivationCandidate,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.models.automation import AutomationFlow
from shared import get_logger

logger = get_logger(__name__)

# Conservative scan floor (days): the per-merchant dormancy threshold, read from
# the `lead_dormant` trigger node, is enforced per candidate in the loop.
_MIN_DORMANT_DAYS = 30
_DEFAULT_DORMANT_DAYS = 90


async def reactivate_dormant_leads(ctx: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    candidates = await _scan_candidates(dormant_cutoff=now - timedelta(days=_MIN_DORMANT_DAYS))
    logger.info("uc06.scan", count=len(candidates))

    emitted = 0
    for cand in candidates:
        if await _maybe_emit(cand, now=now):
            emitted += 1

    return {"candidates": len(candidates), "emitted": emitted}


async def _scan_candidates(*, dormant_cutoff: datetime) -> list[ReactivationCandidate]:
    async with session_scope() as session:
        return await LeadRepository(session).list_reactivation_candidates(
            dormant_cutoff=dormant_cutoff
        )


async def _maybe_emit(cand: ReactivationCandidate, *, now: datetime) -> bool:
    # Edge gate: fire once per dormancy episode, keyed on last_interaction_at.
    if cand.dormant_fired_for is not None and cand.dormant_fired_for >= cand.last_interaction_at:
        return False

    tenant_ctx = TenantContext(
        tenant_id=cand.tenant_id,
        merchant_id=cand.merchant_id,
        role="worker",
        actor_id=cand.merchant_id,
    )
    async with tenant_session(tenant_ctx) as session:
        autos = await AutomationRepository(session).list_enabled_by_trigger(
            merchant_id=cand.merchant_id, trigger_type="lead_dormant"
        )
        if not autos:
            return False

        days = _threshold_days(autos)
        if now - cand.last_interaction_at < timedelta(days=days):
            return False

        await LeadRepository(session).mark_dormant_fired(cand.lead_id, cand.last_interaction_at)
        await AnalyticsRepository(session).emit(
            tenant_id=cand.tenant_id,
            merchant_id=cand.merchant_id,
            event_type="lead.dormant",
            subject_type="lead",
            subject_id=cand.lead_id,
            properties={
                "days_dormant": int((now - cand.last_interaction_at).total_seconds() / 86400),
                # Re-engagement anchor for the engine's stale-cadence guard.
                "episode_anchor": (cand.last_inbound_at or cand.last_interaction_at).isoformat(),
            },
        )
        logger.info("uc06.emitted", lead_id=str(cand.lead_id))
    return True


def _threshold_days(autos: list[AutomationFlow]) -> int:
    """Smallest `lead_dormant` trigger threshold (days) across the enabled
    automations. Falls back to the config-era default when none set it."""
    values: list[int] = []
    for auto in autos:
        trigger = next((n for n in auto.nodes if n.kind == "trigger"), None)
        days = (trigger.config or {}).get("days") if trigger else None
        if isinstance(days, (int, float)) and days > 0:
            values.append(int(days))
    return min(values) if values else _DEFAULT_DORMANT_DAYS
