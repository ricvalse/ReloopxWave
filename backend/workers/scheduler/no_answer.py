"""UC-03 — follow-up for leads that went quiet.

Job cadence: every 15 minutes (configured in Railway Cron, not here). Every
tick we:
  1. Scan (admin session) for active conversations idle > 30 min with fewer
     than max_followups reminders sent so far.
  2. Per candidate, resolve the merchant's per-config `no_answer.*` thresholds
     via the config cascade and decide whether the next reminder is due.
  3. Resolve WhatsApp integration, send the reminder, stamp the conversation.

Idempotency: a Redis dedup key per (conversation, attempt) stops us sending
the same reminder twice even if the job overlaps with itself.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from config_resolver import ConfigKey, ConfigResolver
from db import (
    AnalyticsRepository,
    ConversationRepository,
    IntegrationRepository,
    ReminderCandidate,
    TenantContext,
    session_scope,
    tenant_session,
)
from integrations.whatsapp.factory import build_whatsapp_sender
from shared import get_logger

logger = get_logger(__name__)

REMINDER_TEXTS = {
    1: "Ciao! Eri ancora interessato? Se vuoi posso aiutarti a completare la richiesta.",
    2: "Facciamo un ultimo tentativo — se vuoi riprendere la conversazione, rispondi pure.",
}

DEDUP_TTL_SECONDS = 60 * 60 * 24 * 3  # 3 days — longer than the second-reminder window


async def followup_no_answer(ctx: dict) -> dict:
    settings = ctx["settings"]
    redis: Redis = ctx.get("redis") or Redis.from_url(settings.redis_url)

    # Default max_followups is 2; per-merchant overrides are checked inside the loop.
    candidates = await _scan_candidates(max_followups=4)
    logger.info("uc03.scan", count=len(candidates))

    sent = 0
    for cand in candidates:
        did_send = await _maybe_send_reminder(cand, redis=redis, kek=settings.integrations_kek_base64)
        if did_send:
            sent += 1

    return {"candidates": len(candidates), "sent": sent}


async def _scan_candidates(*, max_followups: int) -> list[ReminderCandidate]:
    async with session_scope() as session:
        repo = ConversationRepository(session)
        return await repo.list_reminder_candidates(max_followups=max_followups)


async def _maybe_send_reminder(
    cand: ReminderCandidate, *, redis: Redis, kek: str
) -> bool:
    now = datetime.now(tz=UTC)
    tenant_ctx = TenantContext(
        tenant_id=cand.tenant_id,
        merchant_id=cand.merchant_id,
        role="worker",
        actor_id=cand.merchant_id,
    )

    async with tenant_session(tenant_ctx) as session:
        config = ConfigResolver(session)
        first_min = _as_int(
            await config.resolve(ConfigKey.NO_ANSWER_FIRST_REMINDER_MIN, merchant_id=cand.merchant_id),
            120,
        )
        second_min = _as_int(
            await config.resolve(ConfigKey.NO_ANSWER_SECOND_REMINDER_MIN, merchant_id=cand.merchant_id),
            1440,
        )
        max_attempts = _as_int(
            await config.resolve(ConfigKey.NO_ANSWER_MAX_FOLLOWUPS, merchant_id=cand.merchant_id),
            2,
        )

        next_attempt = cand.reminders_sent + 1
        if next_attempt > max_attempts:
            return False

        threshold_min = first_min if next_attempt == 1 else second_min
        reference = cand.last_reminder_at or cand.last_message_at
        if now - reference < timedelta(minutes=threshold_min):
            return False

        # Redis dedup — one worker wins the send even if the scan returned dupes.
        dedup_key = f"noanswer:{cand.conversation_id}:{next_attempt}"
        acquired = await redis.set(dedup_key, "1", nx=True, ex=DEDUP_TTL_SECONDS)
        if not acquired:
            return False

        integrations = IntegrationRepository(session, kek_base64=kek)
        wa = await integrations.resolve_whatsapp(cand.wa_phone_number_id)
        if wa is None:
            logger.info("uc03.no_wa_integration", conversation_id=str(cand.conversation_id))
            return False

        text = REMINDER_TEXTS.get(next_attempt, REMINDER_TEXTS[max(REMINDER_TEXTS)])

        # Send first, then persist — if the provider fails, we'd rather retry
        # than leave a ghost "reminder sent" row. Dedup key prevents duplicate
        # sends within TTL.
        client = build_whatsapp_sender(
            phone_number_id=wa.phone_number_id, api_key=wa.api_key
        )
        try:
            await client.send_text(to_phone=cand.wa_contact_phone, text=text)
        finally:
            await client.close()

        convs = ConversationRepository(session)
        await convs.record_reminder_sent(cand.conversation_id)

        analytics = AnalyticsRepository(session)
        await analytics.emit(
            tenant_id=cand.tenant_id,
            merchant_id=cand.merchant_id,
            event_type="reminder.sent",
            subject_type="conversation",
            subject_id=cand.conversation_id,
            properties={"attempt": next_attempt, "idle_minutes": int((now - reference).total_seconds() / 60)},
        )
        return True


def _as_int(value, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default
