"""Read-model / aggregation correctness for the dashboards (UC-11 merchant,
UC-12 agency) and the daily KPI rollup worker.

Runs against Postgres (auto-skips without SUPABASE_DB_URL). Seeds known rows
through `session_scope` (superuser) and asserts the aggregations match the
seeded counts — independent of RLS (these are server-side worker/read paths).

Covers:
  * AnalyticsRepository.merchant_kpis — lead totals, hot-lead count, the event
    counters, response_rate and booking_rate derivation, campaign scoping.
  * AnalyticsRepository.tenant_totals — tenant-wide leads / active merchants /
    event counters.
  * daily_kpi_rollup — yesterday's per-merchant synthetic events
    (messages_received / conversations / hot_leads) with correct counts.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from workers.scheduler.kpi_rollup import daily_kpi_rollup

from db import session_scope
from db.models import AnalyticsEvent, Conversation, Lead, Merchant, Message
from db.repositories.analytics import AnalyticsRepository

pytestmark = pytest.mark.asyncio

TwoTenants = tuple[object, Merchant, object, Merchant]


async def _add_lead(merchant_id: uuid.UUID, *, score: int = 0, campaign: str | None = None) -> None:
    async with session_scope() as session:
        session.add(
            Lead(
                merchant_id=merchant_id,
                phone=f"39{uuid.uuid4().int % 10**9:09d}",
                score=score,
                campaign=campaign,
            )
        )
        await session.flush()


async def _emit(
    tenant_id: uuid.UUID, merchant_id: uuid.UUID, event_type: str, *, n: int = 1
) -> None:
    async with session_scope() as session:
        repo = AnalyticsRepository(session)
        for _ in range(n):
            await repo.emit(
                tenant_id=tenant_id,
                merchant_id=merchant_id,
                event_type=event_type,
            )


# --- merchant_kpis (UC-11) --------------------------------------------------


async def test_merchant_kpis_counts_and_rates(two_tenants: TwoTenants) -> None:
    t1, m1, _t2, _m2 = two_tenants
    await _add_lead(m1.id, score=90)  # hot
    await _add_lead(m1.id, score=90)  # hot
    await _add_lead(m1.id, score=10)  # cold
    await _emit(t1.id, m1.id, "message.received", n=10)
    await _emit(t1.id, m1.id, "message.replied", n=5)
    await _emit(t1.id, m1.id, "booking.created", n=3)
    await _emit(t1.id, m1.id, "reminder.sent", n=2)

    async with session_scope() as session:
        kpis = await AnalyticsRepository(session).merchant_kpis(merchant_id=m1.id)

    assert kpis.leads_total == 3
    assert kpis.leads_hot == 2
    assert kpis.messages_received == 10
    assert kpis.messages_replied == 5
    assert kpis.bookings_created == 3
    assert kpis.reminders_sent == 2
    assert kpis.response_rate == pytest.approx(0.5)
    assert kpis.booking_rate == pytest.approx(3 / 3)


async def test_merchant_kpis_zero_division_is_safe(two_tenants: TwoTenants) -> None:
    _t1, m1, _t2, _m2 = two_tenants
    async with session_scope() as session:
        kpis = await AnalyticsRepository(session).merchant_kpis(merchant_id=m1.id)
    assert kpis.leads_total == 0
    assert kpis.response_rate == 0.0
    assert kpis.booking_rate == 0.0


async def test_merchant_kpis_campaign_scopes_lead_metrics(two_tenants: TwoTenants) -> None:
    _t1, m1, _t2, _m2 = two_tenants
    await _add_lead(m1.id, score=90, campaign="estate")
    await _add_lead(m1.id, score=10, campaign="inverno")

    async with session_scope() as session:
        kpis = await AnalyticsRepository(session).merchant_kpis(
            merchant_id=m1.id, campaign="estate"
        )
    # Only the "estate" lead is counted.
    assert kpis.leads_total == 1
    assert kpis.leads_hot == 1


# --- tenant_totals (UC-12) --------------------------------------------------


async def test_tenant_totals_aggregates_tenant_wide(two_tenants: TwoTenants) -> None:
    t1, m1, _t2, _m2 = two_tenants
    await _add_lead(m1.id)
    await _add_lead(m1.id)
    await _emit(t1.id, m1.id, "message.received", n=4)
    await _emit(t1.id, m1.id, "booking.created", n=1)

    async with session_scope() as session:
        totals = await AnalyticsRepository(session).tenant_totals(tenant_id=t1.id)

    assert totals["leads_total"] == 2
    assert totals["messages_received"] == 4
    assert totals["bookings_created"] == 1
    # The seeded merchant is active by default → counted.
    assert totals["active_merchants"] >= 1


async def test_tenant_totals_isolated_from_other_tenant(two_tenants: TwoTenants) -> None:
    t1, m1, t2, m2 = two_tenants
    await _add_lead(m1.id)
    await _emit(t1.id, m1.id, "message.received", n=3)
    # Other tenant's data must not leak into t1's totals.
    await _add_lead(m2.id)
    await _emit(t2.id, m2.id, "message.received", n=99)

    async with session_scope() as session:
        totals = await AnalyticsRepository(session).tenant_totals(tenant_id=t1.id)
    assert totals["leads_total"] == 1
    assert totals["messages_received"] == 3


# --- daily_kpi_rollup -------------------------------------------------------


async def test_daily_kpi_rollup_writes_yesterday_counts(two_tenants: TwoTenants) -> None:
    _t1, m1, _t2, _m2 = two_tenants
    yesterday = datetime.now(tz=UTC).replace(
        hour=12, minute=0, second=0, microsecond=0
    ) - timedelta(days=1)

    async with session_scope() as session:
        conv = Conversation(merchant_id=m1.id, status="active", created_at=yesterday)
        session.add(conv)
        await session.flush()
        for i in range(3):
            session.add(
                Message(
                    conversation_id=conv.id,
                    merchant_id=m1.id,
                    role="user",
                    direction="in",
                    content=f"msg {i}",
                    created_at=yesterday,
                )
            )
        # A hot lead updated yesterday → counted in hot_leads.
        session.add(
            Lead(
                merchant_id=m1.id,
                phone=f"39{uuid.uuid4().int % 10**9:09d}",
                score=95,
                updated_at=yesterday,
            )
        )
        await session.flush()

    result = await daily_kpi_rollup({})
    assert result["events_written"] >= 3  # 3 metrics per merchant touched

    async with session_scope() as session:
        events = (
            (
                await session.execute(
                    select(AnalyticsEvent).where(
                        AnalyticsEvent.merchant_id == m1.id,
                        AnalyticsEvent.event_type.like("kpi.daily.%"),
                    )
                )
            )
            .scalars()
            .all()
        )

    by_type = {e.event_type: e.properties.get("count") for e in events}
    assert by_type.get("kpi.daily.messages_received") == 3
    assert by_type.get("kpi.daily.conversations") == 1
    assert by_type.get("kpi.daily.hot_leads") == 1
