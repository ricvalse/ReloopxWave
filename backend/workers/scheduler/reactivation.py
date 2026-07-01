"""UC-06 — reactivate dormant leads.

Scans leads whose most-recent conversation is older than
`reactivation.dormant_days` (per-merchant config), hasn't exceeded
`reactivation.max_attempts`, and spaces each attempt by `interval_days`.
Sends a reactivation WhatsApp message, stamps the lead.

Idempotency: Redis dedup per (lead, attempt) just like UC-03.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis

from ai_core.automations import REACTIVATION_MAX_SENDS
from config_resolver import ConfigKey, ConfigResolver
from db import (
    FLOW_REACTIVATION,
    AnalyticsRepository,
    ConversationRepository,
    IntegrationRepository,
    LeadRepository,
    ReactivationCandidate,
    TenantContext,
    session_scope,
    tenant_session,
)
from integrations.whatsapp.factory import build_whatsapp_sender
from shared import get_logger
from workers.automation.lifecycle import resolve_lifecycle_plan, resolve_lifecycle_step
from workers.outbound import MODE_SKIP, decide_outbound, send_and_persist_decision

logger = get_logger(__name__)

DEDUP_TTL_SECONDS = 60 * 60 * 24 * 14  # two weeks — covers the longest interval_days default


async def reactivate_dormant_leads(ctx: dict[str, Any]) -> dict[str, Any]:
    settings = ctx["settings"]
    redis: Redis = ctx.get("redis") or Redis.from_url(settings.redis_url)

    now = datetime.now(tz=UTC)
    # Use conservative floors (min dormant, min interval) so the scan returns only
    # leads that *could* be due. Per-merchant thresholds are enforced in the loop.
    min_dormant_cutoff = now - timedelta(days=30)
    min_interval_cutoff = now - timedelta(days=3)

    candidates = await _scan_candidates(
        dormant_cutoff=min_dormant_cutoff,
        interval_cutoff=min_interval_cutoff,
        max_attempts=5,
    )
    logger.info("uc06.scan", count=len(candidates))

    sent = 0
    for cand in candidates:
        if await _maybe_send(cand, now=now, redis=redis, kek=settings.integrations_kek_base64):
            sent += 1

    return {"candidates": len(candidates), "sent": sent}


async def _scan_candidates(
    *, dormant_cutoff: datetime, interval_cutoff: datetime, max_attempts: int
) -> list[ReactivationCandidate]:
    async with session_scope() as session:
        return await LeadRepository(session).list_reactivation_candidates(
            dormant_cutoff=dormant_cutoff,
            interval_cutoff=interval_cutoff,
            max_attempts=max_attempts,
        )


async def _maybe_send(
    cand: ReactivationCandidate, *, now: datetime, redis: Redis, kek: str
) -> bool:
    tenant_ctx = TenantContext(
        tenant_id=cand.tenant_id,
        merchant_id=cand.merchant_id,
        role="worker",
        actor_id=cand.merchant_id,
    )

    async with tenant_session(tenant_ctx) as session:
        config = ConfigResolver(session)
        dormant_days = _as_int(
            await config.resolve(ConfigKey.REACTIVATION_DORMANT_DAYS, merchant_id=cand.merchant_id),
            90,
        )
        interval_days = _as_int(
            await config.resolve(
                ConfigKey.REACTIVATION_INTERVAL_DAYS, merchant_id=cand.merchant_id
            ),
            7,
        )
        config_max = _as_int(
            await config.resolve(ConfigKey.REACTIVATION_MAX_ATTEMPTS, merchant_id=cand.merchant_id),
            3,
        )

        # ADR 0011: an ENABLED system flow sources the timing/cadence/count from
        # the canvas; otherwise fall back to the ConfigKeys above (compat).
        # Dormant leads are always outside the 24h window; conditions can branch on
        # the lead's score (carried by the candidate) and the time of day.
        temperature = "hot" if cand.score >= 80 else "warm" if cand.score >= 40 else "cold"
        minutes_of_day = now.hour * 60 + now.minute
        plan_context = {
            "within_24h_window": False,
            "minutes_of_day": minutes_of_day,
            "score": cand.score,
            "temperature": temperature,
        }
        plan = await resolve_lifecycle_plan(
            session,
            merchant_id=cand.merchant_id,
            system_key=FLOW_REACTIVATION,
            context=plan_context,
        )
        graph_sends = plan.sends if plan else []
        max_attempts = min(len(graph_sends), REACTIVATION_MAX_SENDS) if graph_sends else config_max

        if cand.attempts_sent >= max_attempts:
            return False

        next_attempt = cand.attempts_sent + 1

        # Dormant threshold (trigger → 1st attempt): the graph's first-send delay
        # (folded from trigger.days) wins when set, else the config dormant_days.
        dormant_delta = timedelta(days=dormant_days)
        if graph_sends and graph_sends[0].delay_minutes > 0:
            dormant_delta = timedelta(minutes=graph_sends[0].delay_minutes)
        if now - cand.last_interaction_at < dormant_delta:
            return False

        # Interval between attempts (attempt ≥ 2): the graph's per-send `wait` wins.
        interval_delta = timedelta(days=interval_days)
        if graph_sends and len(graph_sends) >= next_attempt and next_attempt >= 2:
            graph_gap = graph_sends[next_attempt - 1].delay_minutes
            if graph_gap > 0:
                interval_delta = timedelta(minutes=graph_gap)
        if (
            cand.last_reactivation_at is not None
            and now - cand.last_reactivation_at < interval_delta
        ):
            return False

        # Decide before the dedup so a skip (no template yet) can be retried once
        # one is approved.
        step = await resolve_lifecycle_step(
            session,
            merchant_id=cand.merchant_id,
            system_key=FLOW_REACTIVATION,
            attempt_index=next_attempt - 1,
            context=plan_context,
        )
        # No hardcoded copy: the reactivation text comes solely from the send
        # node's `free_text` on the lavagnetta (via `step`), which renders `{name}`
        # / `{{contact.name}}` from the context below. A blank send node → skip.
        analytics = AnalyticsRepository(session)
        decision = decide_outbound(
            within_window=False,
            step=step,
            context={"contact.phone": cand.phone, "contact.name": cand.name or ""},
        )
        if decision.mode == MODE_SKIP:
            logger.info(
                "uc06.skipped",
                lead_id=str(cand.lead_id),
                reason=decision.reason,
            )
            await analytics.emit(
                tenant_id=cand.tenant_id,
                merchant_id=cand.merchant_id,
                event_type="lead_reactivation.skipped",
                subject_type="lead",
                subject_id=cand.lead_id,
                properties={"attempt": next_attempt, "reason": decision.reason},
            )
            return False

        # Resolve the integration BEFORE consuming the dedup key — a missing
        # channel must not burn the attempt's key for the full TTL (14 days here)
        # and silently lose the reactivation.
        integrations = IntegrationRepository(session, kek_base64=kek)
        wa = await integrations.resolve_whatsapp(cand.wa_phone_number_id)
        if wa is None:
            logger.info(
                "uc06.no_wa_integration",
                lead_id=str(cand.lead_id),
                wa_phone_number_id=cand.wa_phone_number_id,
            )
            return False

        dedup_key = f"reactivate:{cand.lead_id}:{next_attempt}"
        if not await redis.set(dedup_key, "1", nx=True, ex=DEDUP_TTL_SECONDS):
            return False

        # Resolve (or open) the lead's conversation so the reactivation lands in
        # the inbox and the delivery callback can attach to its Message row.
        convs = ConversationRepository(session)
        conv = await convs.get_active(merchant_id=cand.merchant_id, wa_contact_phone=cand.phone)
        if conv is None:
            conv = await convs.create(
                merchant_id=cand.merchant_id,
                lead_id=cand.lead_id,
                wa_phone_number_id=cand.wa_phone_number_id,
                wa_contact_phone=cand.phone,
            )

        client = build_whatsapp_sender(
            phone_number_id=wa.phone_number_id,
            api_key=wa.api_key,
            waba_base_url=wa.waba_base_url,
        )
        try:
            await send_and_persist_decision(
                client,
                to_phone=cand.phone,
                decision=decision,
                session=session,
                conversation_id=conv.id,
                merchant_id=cand.merchant_id,
                sender_type="reactivation",
            )
        finally:
            await client.close()

        await LeadRepository(session).record_reactivation_sent(cand.lead_id)
        await analytics.emit(
            tenant_id=cand.tenant_id,
            merchant_id=cand.merchant_id,
            event_type="lead_reactivation.sent",
            subject_type="lead",
            subject_id=cand.lead_id,
            properties={
                "attempt": next_attempt,
                "days_dormant": int((now - cand.last_interaction_at).total_seconds() / 86400),
                "mode": decision.mode,
            },
        )
        return True


def _as_int(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default
