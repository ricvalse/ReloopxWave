"""GDPR retention enforcement — purge conversation PII past its retention window.

`privacy.retention_months` (config cascade, default 24, range 6-60) defines how
long a merchant keeps conversation data. This daily sweep deletes conversations
(and their messages, via FK cascade) whose last activity predates the merchant's
cutoff. Until this existed the setting was surfaced in the UI but never enforced.

Scope: conversation + message content (the bulk of stored PII). Lead-record PII
(name/email/phone) is erased on request via the DSAR endpoints; aging out
lead records wholesale is a deliberate follow-up, not done here.

Runs under the service-role session (cross-merchant) with an explicit
`merchant_id` filter on every delete, mirroring `close_idle_conversations`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from config_resolver import ConfigKey, ConfigResolver
from db import (
    AnalyticsRepository,
    ConversationRepository,
    LeadRepository,
    session_scope,
)
from shared import get_logger

logger = get_logger(__name__)

# The schema floor for retention is 6 months — only merchants with data older
# than that can possibly have anything to purge, so scan from there.
MIN_RETENTION_MONTHS = 6
_DAYS_PER_MONTH = 30  # retention windows are coarse; month≈30d is acceptable
_BATCH = 2000  # per delete call (bounds each statement)
_RUN_BUDGET = 100_000  # max conversations deleted per nightly run (bounds runtime)


async def enforce_retention(ctx: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    floor_cutoff = now - timedelta(days=MIN_RETENTION_MONTHS * _DAYS_PER_MONTH)

    merchants = 0
    total_deleted = 0
    total_leads_anonymized = 0
    budget_hit = False
    async with session_scope() as session:
        convs = ConversationRepository(session)
        leads = LeadRepository(session)
        config = ConfigResolver(session)
        analytics = AnalyticsRepository(session)

        candidates = await convs.merchants_with_conversations_before(floor_cutoff)
        merchants = len(candidates)
        for merchant_id, tenant_id in candidates:
            # Clamp to the schema floor: a stray 0/negative retention value must
            # never collapse the cutoff to "now" and wipe a merchant's history.
            months = max(
                MIN_RETENTION_MONTHS,
                _as_int(
                    await config.resolve(
                        ConfigKey.PRIVACY_RETENTION_MONTHS, merchant_id=merchant_id
                    ),
                    24,
                ),
            )
            cutoff = now - timedelta(days=months * _DAYS_PER_MONTH)

            # Drain the merchant's backlog in bounded batches, committing each so
            # the daily run actually clears high-volume merchants instead of
            # trimming a fixed slice and leaving data past its window for days.
            merchant_deleted = 0
            while total_deleted < _RUN_BUDGET:
                n = await convs.delete_older_than(
                    merchant_id=merchant_id, cutoff=cutoff, limit=_BATCH
                )
                if n == 0:
                    break
                merchant_deleted += n
                total_deleted += n
                await session.commit()
                if n < _BATCH:
                    break
            else:
                budget_hit = True

            # Lead-level PII: after the merchant's old conversations are gone,
            # strip PII from leads past the cutoff that have no surviving
            # conversation (idempotent; recent/kept leads are untouched).
            leads_anon = await leads.anonymize_stale(merchant_id=merchant_id, cutoff=cutoff)
            if leads_anon:
                total_leads_anonymized += leads_anon
                await session.commit()

            if merchant_deleted or leads_anon:
                await analytics.emit(
                    tenant_id=tenant_id,
                    merchant_id=merchant_id,
                    event_type="retention.purged",
                    subject_type="merchant",
                    subject_id=merchant_id,
                    properties={
                        "conversations_deleted": merchant_deleted,
                        "leads_anonymized": leads_anon,
                        "retention_months": months,
                        "cutoff": cutoff.isoformat(),
                    },
                )
            if total_deleted >= _RUN_BUDGET:
                budget_hit = True
                break
        # Commit on context exit.

    logger.info(
        "retention.sweep",
        merchants=merchants,
        conversations_deleted=total_deleted,
        leads_anonymized=total_leads_anonymized,
        budget_hit=budget_hit,
    )
    return {
        "merchants": merchants,
        "conversations_deleted": total_deleted,
        "leads_anonymized": total_leads_anonymized,
        "budget_hit": budget_hit,
    }


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default
