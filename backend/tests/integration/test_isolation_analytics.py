"""Intra-tenant RLS isolation for analytics_events (migration 0019).

The 0001 policy was tenant-only, so two merchants under the SAME agency tenant
could read each other's analytics. 0019 makes the policy tenant-OR-merchant.
These tests seed one tenant with two merchants and assert a merchant-scoped
session sees only its own events, while an agency session (no merchant claim)
sees both.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select

from db import TenantContext, session_scope, tenant_session
from db.models import Merchant, Tenant
from db.models.analytics import AnalyticsEvent

pytestmark = pytest.mark.asyncio

OneTenantTwoMerchants = tuple[uuid.UUID, uuid.UUID, uuid.UUID]


@pytest_asyncio.fixture
async def one_tenant_two_merchants() -> AsyncIterator[OneTenantTwoMerchants]:
    suffix = uuid.uuid4().hex[:8]
    t = Tenant(slug=f"t-{suffix}", name=f"Tenant {suffix}")
    async with session_scope() as session:
        session.add(t)
        await session.flush()
        m1 = Merchant(tenant_id=t.id, slug=f"m1-{suffix}", name=f"M1 {suffix}")
        m2 = Merchant(tenant_id=t.id, slug=f"m2-{suffix}", name=f"M2 {suffix}")
        session.add_all([m1, m2])
        await session.flush()
        t_id, m1_id, m2_id = t.id, m1.id, m2.id
        # Seed one analytics event per merchant (service-role bypasses RLS).
        session.add_all(
            [
                AnalyticsEvent(tenant_id=t_id, merchant_id=m1_id, event_type="booking.created"),
                AnalyticsEvent(tenant_id=t_id, merchant_id=m2_id, event_type="booking.created"),
            ]
        )

    try:
        yield t_id, m1_id, m2_id
    finally:
        async with session_scope() as session:
            row = await session.get(Tenant, t_id)
            if row is not None:
                await session.delete(row)


def _merchant_ctx(tenant_id: uuid.UUID, merchant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        role="merchant_admin",
        actor_id=uuid.uuid4(),
    )


def _agency_ctx(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id, merchant_id=None, role="agency_admin", actor_id=uuid.uuid4()
    )


async def test_merchant_sees_only_own_analytics(
    one_tenant_two_merchants: OneTenantTwoMerchants,
) -> None:
    t_id, m1_id, m2_id = one_tenant_two_merchants

    async with tenant_session(_merchant_ctx(t_id, m1_id)) as session:
        rows = (await session.execute(select(AnalyticsEvent))).scalars().all()
        merchant_ids = {r.merchant_id for r in rows}

    assert m1_id in merchant_ids
    assert m2_id not in merchant_ids


async def test_agency_sees_all_tenant_analytics(
    one_tenant_two_merchants: OneTenantTwoMerchants,
) -> None:
    t_id, m1_id, m2_id = one_tenant_two_merchants

    async with tenant_session(_agency_ctx(t_id)) as session:
        rows = (await session.execute(select(AnalyticsEvent))).scalars().all()
        merchant_ids = {r.merchant_id for r in rows}

    assert {m1_id, m2_id} <= merchant_ids
