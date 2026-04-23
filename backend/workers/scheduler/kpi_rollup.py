"""Daily KPI rollup — writes pre-aggregated rows into `analytics_events` so
the agency/merchant dashboards don't re-scan raw rows on every request.

The rollup emits one synthetic event per `(tenant_id, merchant_id, metric)`
triple for the previous day. Event type is `kpi.daily.<metric>` so the
existing AnalyticsRepository queries can filter on it without schema change.

Idempotency: running twice for the same day produces duplicate events. A
follow-up adds a UNIQUE(tenant_id, merchant_id, event_type, occurred_at)
index on the analytics_events table; for now the scheduler runs at most
once a day so double-emit is bounded.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from db import session_scope
from db.models import AnalyticsEvent, Conversation, Lead, Merchant, Message
from shared import get_logger

logger = get_logger(__name__)


async def daily_kpi_rollup(ctx: dict[str, Any]) -> dict[str, Any]:
    """Compute yesterday's per-merchant KPIs and persist them as synthetic
    events. Returns a summary so ARQ logs show what we wrote.
    """
    end = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=1)

    written = 0
    tenants_touched: set[str] = set()

    async with session_scope() as session:
        rows_stmt = (
            select(
                Merchant.id,
                Merchant.tenant_id,
                func.count(Message.id).label("messages_received"),
                func.count(Conversation.id.distinct()).label("conversations"),
            )
            .select_from(Merchant)
            .join(Message, Message.merchant_id == Merchant.id, isouter=True)
            .join(Conversation, Conversation.merchant_id == Merchant.id, isouter=True)
            .where(
                (Message.created_at.between(start, end)) | (Message.created_at.is_(None)),
            )
            .group_by(Merchant.id, Merchant.tenant_id)
        )
        rows = (await session.execute(rows_stmt)).all()

        hot_stmt = (
            select(Lead.merchant_id, func.count(Lead.id).label("hot"))
            .where(Lead.score >= 80, Lead.updated_at.between(start, end))
            .group_by(Lead.merchant_id)
        )
        hot_rows = (await session.execute(hot_stmt)).all()
        hot_by_merchant: dict[Any, int] = {row[0]: int(row[1]) for row in hot_rows}

        now = datetime.now(tz=UTC)
        for merchant_id, tenant_id, messages_received, conversations in rows:
            tenants_touched.add(str(tenant_id))
            session.add(
                AnalyticsEvent(
                    tenant_id=tenant_id,
                    merchant_id=merchant_id,
                    event_type="kpi.daily.messages_received",
                    subject_type="merchant",
                    subject_id=merchant_id,
                    properties={
                        "count": int(messages_received or 0),
                        "day": start.date().isoformat(),
                    },
                    occurred_at=now,
                )
            )
            session.add(
                AnalyticsEvent(
                    tenant_id=tenant_id,
                    merchant_id=merchant_id,
                    event_type="kpi.daily.conversations",
                    subject_type="merchant",
                    subject_id=merchant_id,
                    properties={
                        "count": int(conversations or 0),
                        "day": start.date().isoformat(),
                    },
                    occurred_at=now,
                )
            )
            session.add(
                AnalyticsEvent(
                    tenant_id=tenant_id,
                    merchant_id=merchant_id,
                    event_type="kpi.daily.hot_leads",
                    subject_type="merchant",
                    subject_id=merchant_id,
                    properties={
                        "count": int(hot_by_merchant.get(merchant_id, 0)),
                        "day": start.date().isoformat(),
                    },
                    occurred_at=now,
                )
            )
            written += 3

    logger.info(
        "kpi.daily.rollup.done",
        day=start.date().isoformat(),
        events_written=written,
        tenants=len(tenants_touched),
    )
    return {
        "day": start.date().isoformat(),
        "events_written": written,
        "tenants": len(tenants_touched),
    }
