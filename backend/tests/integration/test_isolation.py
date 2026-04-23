"""Cross-tenant RLS isolation guarantees (§15 commitment).

These tests run two JWT-shaped contexts against a real Postgres and assert
that tenant A cannot observe tenant B's rows through any of:

  * a bulk SELECT (silent filter to zero rows)
  * a primary-key `session.get()` (identity-map bypass guarded by FORCE RLS)
  * an UPDATE WHERE clause (row count zero)

The same expectations apply to the API surface, but we exercise the DB layer
directly here because a failure here means every router is leaking regardless
of its own filtering.
"""

from __future__ import annotations

import uuid
from typing import cast

import pytest
from sqlalchemy import CursorResult, select, update

from db import MerchantRepository, TenantContext, TenantRepository, tenant_session
from db.models import Merchant, Tenant

pytestmark = pytest.mark.asyncio

TwoTenants = tuple[Tenant, Merchant, Tenant, Merchant]


async def test_tenant_cannot_read_other_tenant_merchants(two_tenants: TwoTenants) -> None:
    t1, m1, _t2, m2 = two_tenants

    async with tenant_session(_admin_ctx(t1.id)) as session:
        repo = MerchantRepository(session)
        merchants = await repo.list_for_tenant(t1.id)
        merchant_ids = {m.id for m in merchants}

        assert m1.id in merchant_ids
        assert m2.id not in merchant_ids

        # Even a pk lookup for the foreign merchant must return None under RLS.
        foreign = await repo.get(m2.id)
        assert foreign is None


async def test_tenant_cannot_list_other_tenant_rows_via_raw_select(
    two_tenants: TwoTenants,
) -> None:
    t1, _m1, t2, _m2 = two_tenants

    async with tenant_session(_admin_ctx(t1.id)) as session:
        rows = (await session.execute(select(Tenant))).scalars().all()
        tenant_ids = {t.id for t in rows}

    assert t1.id in tenant_ids
    assert t2.id not in tenant_ids


async def test_tenant_update_of_foreign_merchant_changes_nothing(
    two_tenants: TwoTenants,
) -> None:
    t1, _m1, t2, m2 = two_tenants

    async with tenant_session(_admin_ctx(t1.id)) as session:
        result = cast(
            "CursorResult[tuple[Merchant]]",
            await session.execute(
                update(Merchant).where(Merchant.id == m2.id).values(name="hijacked")
            ),
        )
        assert result.rowcount == 0

    # Verify from the legitimate owner that nothing changed.
    async with tenant_session(_admin_ctx(t2.id)) as session:
        reread = await session.get(Merchant, m2.id)
        assert reread is not None
        assert reread.name != "hijacked"


async def test_tenant_sees_only_own_row_via_tenant_repository(two_tenants: TwoTenants) -> None:
    t1, _m1, t2, _m2 = two_tenants

    async with tenant_session(_admin_ctx(t1.id)) as session:
        visible = await TenantRepository(session).list_visible()

    visible_ids = {t.id for t in visible}
    assert visible_ids == {t1.id}, f"Expected only tenant {t1.id} visible, got {visible_ids}"
    assert t2.id not in visible_ids


def _admin_ctx(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        merchant_id=None,
        role="agency_admin",
        actor_id=uuid.uuid4(),
    )
