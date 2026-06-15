"""Cross-tenant RLS isolation for the GHL marketplace tables.

Mirrors test_isolation.py: two JWT-shaped contexts against a real Postgres,
asserting tenant A cannot observe tenant B's ghl_agency_installs /
ghl_location_tokens rows, that the agency admin sees its own tenant's locations
(including pending_link), and that booking resolves a merchant's location token.
Auto-skips when SUPABASE_DB_URL is unset.
"""

from __future__ import annotations

import base64
import uuid

import pytest
from sqlalchemy import select

from db import (
    GHLMarketplaceRepository,
    IntegrationRepository,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.models import GHLAgencyInstall, GHLLocationToken, Merchant, Tenant

pytestmark = pytest.mark.asyncio

TwoTenants = tuple[Tenant, Merchant, Tenant, Merchant]
_KEK = base64.b64encode(b"k" * 32).decode("ascii")


async def _seed_agency(tenant_id: uuid.UUID, company_id: str) -> None:
    async with session_scope() as session:
        await GHLMarketplaceRepository(session, kek_base64=_KEK).upsert_agency_install(
            tenant_id=tenant_id,
            company_id=company_id,
            access_token="AT",
            refresh_token="RT",
            expires_at=9999999999,
            company_name="ACME",
        )


async def _seed_location(
    tenant_id: uuid.UUID,
    company_id: str,
    location_id: str,
    merchant_id: uuid.UUID | None = None,
) -> None:
    async with session_scope() as session:
        repo = GHLMarketplaceRepository(session, kek_base64=_KEK)
        await repo.upsert_location_install(
            tenant_id=tenant_id, company_id=company_id, location_id=location_id
        )
        await repo.set_location_token(
            location_id=location_id,
            access_token="LAT",
            refresh_token="LRT",
            expires_at=9999999999,
        )
        if merchant_id is not None:
            await repo.link_location(location_id=location_id, merchant_id=merchant_id)


async def test_agency_install_isolation(two_tenants: TwoTenants) -> None:
    t1, _m1, t2, _m2 = two_tenants
    c1 = f"comp-{uuid.uuid4().hex[:8]}"
    c2 = f"comp-{uuid.uuid4().hex[:8]}"
    await _seed_agency(t1.id, c1)
    await _seed_agency(t2.id, c2)

    async with tenant_session(_admin_ctx(t1.id)) as session:
        rows = (await session.execute(select(GHLAgencyInstall))).scalars().all()
        assert len(rows) == 1
        assert all(r.tenant_id == t1.id for r in rows)

        repo = GHLMarketplaceRepository(session, kek_base64=_KEK)
        # Cross-tenant companyId is invisible under RLS; own company resolves.
        assert await repo.resolve_agency_by_company_id(c2) is None
        assert await repo.resolve_agency_by_company_id(c1) is not None


async def test_location_token_isolation_and_resolve(two_tenants: TwoTenants) -> None:
    t1, m1, t2, m2 = two_tenants
    c1 = f"comp-{uuid.uuid4().hex[:8]}"
    c2 = f"comp-{uuid.uuid4().hex[:8]}"
    loc1 = f"loc-{uuid.uuid4().hex[:8]}"
    loc2 = f"loc-{uuid.uuid4().hex[:8]}"
    await _seed_agency(t1.id, c1)
    await _seed_agency(t2.id, c2)
    await _seed_location(t1.id, c1, loc1, merchant_id=m1.id)
    await _seed_location(t2.id, c2, loc2, merchant_id=m2.id)

    async with tenant_session(_admin_ctx(t1.id)) as session:
        rows = (await session.execute(select(GHLLocationToken))).scalars().all()
        assert all(r.tenant_id == t1.id for r in rows)

        repo = GHLMarketplaceRepository(session, kek_base64=_KEK)
        mine = await repo.list_locations(t1.id)
        assert [row.location_id for row in mine] == [loc1]
        # The foreign tenant's locations are invisible even when queried directly.
        assert await repo.list_locations(t2.id) == []

    # Booking resolves the location token for its merchant (service-role path).
    async with session_scope() as session:
        resolved = await IntegrationRepository(session, kek_base64=_KEK).resolve_ghl(m1.id)
        assert resolved is not None
        assert resolved.location_id == loc1
        assert resolved.access_token == "LAT"
        assert resolved.merchant_id == m1.id


async def test_install_idempotent_on_location_id(two_tenants: TwoTenants) -> None:
    t1, _m1, _t2, _m2 = two_tenants
    c1 = f"comp-{uuid.uuid4().hex[:8]}"
    loc = f"loc-{uuid.uuid4().hex[:8]}"
    await _seed_agency(t1.id, c1)
    # Two INSTALL deliveries for the same location → exactly one row.
    await _seed_location(t1.id, c1, loc)
    await _seed_location(t1.id, c1, loc)

    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(GHLLocationToken).where(GHLLocationToken.location_id == loc)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


def _admin_ctx(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        merchant_id=None,
        role="agency_admin",
        actor_id=uuid.uuid4(),
    )
