"""UC-06 — reactivate dormant leads.

Scans leads whose most-recent conversation is older than
`reactivation.dormant_days` (per-merchant config), hasn't exceeded
`reactivation.max_attempts`, and spaces each attempt by `interval_days`.
Sends a reactivation WhatsApp message, stamps the lead.

Idempotency: Redis dedup per (lead, attempt) just like UC-03.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from config_resolver import ConfigKey, ConfigResolver
from db import (
    AnalyticsRepository,
    IntegrationRepository,
    LeadRepository,
    ReactivationCandidate,
    TenantContext,
    session_scope,
    tenant_session,
)
from integrations.whatsapp.factory import build_whatsapp_sender
from shared import get_logger

logger = get_logger(__name__)

REACTIVATION_TEXTS = {
    1: "Ciao! È passato un po' — se l'interesse è ancora vivo, possiamo riprendere da dove eravamo?",
    2: "Un ultimo saluto: se vuoi che ti ricontattiamo, rispondi pure a questo messaggio.",
    3: "Ci ripassi volentieri quando ti torna utile. A presto!",
}

DEDUP_TTL_SECONDS = 60 * 60 * 24 * 14  # two weeks — covers the longest interval_days default


async def reactivate_dormant_leads(ctx: dict) -> dict:
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
            await config.resolve(
                ConfigKey.REACTIVATION_DORMANT_DAYS, merchant_id=cand.merchant_id
            ),
            90,
        )
        interval_days = _as_int(
            await config.resolve(
                ConfigKey.REACTIVATION_INTERVAL_DAYS, merchant_id=cand.merchant_id
            ),
            7,
        )
        max_attempts = _as_int(
            await config.resolve(
                ConfigKey.REACTIVATION_MAX_ATTEMPTS, merchant_id=cand.merchant_id
            ),
            3,
        )

        if cand.attempts_sent >= max_attempts:
            return False
        if now - cand.last_interaction_at < timedelta(days=dormant_days):
            return False
        if (
            cand.last_reactivation_at is not None
            and now - cand.last_reactivation_at < timedelta(days=interval_days)
        ):
            return False

        next_attempt = cand.attempts_sent + 1
        dedup_key = f"reactivate:{cand.lead_id}:{next_attempt}"
        if not await redis.set(dedup_key, "1", nx=True, ex=DEDUP_TTL_SECONDS):
            return False

        integrations = IntegrationRepository(session, kek_base64=kek)
        wa = await integrations.resolve_whatsapp(cand.wa_phone_number_id)
        if wa is None:
            logger.info(
                "uc06.no_wa_integration",
                lead_id=str(cand.lead_id),
                wa_phone_number_id=cand.wa_phone_number_id,
            )
            return False

        text = REACTIVATION_TEXTS.get(next_attempt, REACTIVATION_TEXTS[max(REACTIVATION_TEXTS)])

        client = build_whatsapp_sender(
            provider=wa.provider,
            access_token=wa.access_token,
            phone_number_id=wa.phone_number_id,
        )
        try:
            await client.send_text(to_phone=cand.phone, text=text)
        finally:
            await client.close()

        await LeadRepository(session).record_reactivation_sent(cand.lead_id)
        await AnalyticsRepository(session).emit(
            tenant_id=cand.tenant_id,
            merchant_id=cand.merchant_id,
            event_type="lead_reactivation.sent",
            subject_type="lead",
            subject_id=cand.lead_id,
            properties={
                "attempt": next_attempt,
                "days_dormant": int((now - cand.last_interaction_at).total_seconds() / 86400),
            },
        )
        return True


def _as_int(value, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default
