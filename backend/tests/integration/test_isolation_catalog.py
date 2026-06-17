"""Cross-tenant RLS isolation for the catalog tables (products / store_policies
/ faq_entries). Mirrors test_isolation_templates.py: two JWT-shaped contexts
against a real Postgres, asserting tenant A cannot observe tenant B's rows.
Auto-skips when SUPABASE_DB_URL is unset.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from db import (
    FaqRepository,
    ProductRepository,
    StorePolicyRepository,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.models import FaqEntry, Merchant, Product, StorePolicy, Tenant

pytestmark = pytest.mark.asyncio

TwoTenants = tuple[Tenant, Merchant, Tenant, Merchant]


async def _seed_product(merchant_id: uuid.UUID, handle: str) -> uuid.UUID:
    async with session_scope() as session:
        product = Product(
            merchant_id=merchant_id,
            title=f"Prodotto {handle}",
            handle=handle,
            tags=[],
            variants=[],
            images=[],
        )
        session.add(product)
        await session.flush()
        return product.id


async def _seed_faq(merchant_id: uuid.UUID, question: str) -> uuid.UUID:
    async with session_scope() as session:
        entry = FaqEntry(merchant_id=merchant_id, question=question, answer="Risposta.")
        session.add(entry)
        await session.flush()
        return entry.id


async def _seed_policy(merchant_id: uuid.UUID) -> None:
    async with session_scope() as session:
        session.add(StorePolicy(merchant_id=merchant_id, shipping_info="Spedizione 24h"))
        await session.flush()


async def test_tenant_cannot_read_other_tenant_products(two_tenants: TwoTenants) -> None:
    t1, m1, _t2, m2 = two_tenants
    await _seed_product(m1.id, f"a-{uuid.uuid4().hex[:8]}")
    foreign_id = await _seed_product(m2.id, f"b-{uuid.uuid4().hex[:8]}")

    async with tenant_session(_admin_ctx(t1.id)) as session:
        repo = ProductRepository(session)
        assert len(await repo.list_for_merchant(m1.id)) == 1
        assert await repo.list_for_merchant(m2.id) == []

        rows = (await session.execute(select(Product))).scalars().all()
        assert all(r.merchant_id == m1.id for r in rows)

        # PK lookup for the foreign row returns None under FORCE RLS.
        assert await repo.get(foreign_id) is None


async def test_tenant_cannot_read_other_tenant_faq(two_tenants: TwoTenants) -> None:
    t1, m1, _t2, m2 = two_tenants
    await _seed_faq(m1.id, "Domanda mia?")
    foreign_id = await _seed_faq(m2.id, "Domanda altrui?")

    async with tenant_session(_admin_ctx(t1.id)) as session:
        repo = FaqRepository(session)
        assert len(await repo.list_for_merchant(m1.id)) == 1
        assert await repo.list_for_merchant(m2.id) == []
        assert await repo.get(foreign_id) is None

        rows = (await session.execute(select(FaqEntry))).scalars().all()
        assert all(r.merchant_id == m1.id for r in rows)


async def test_tenant_cannot_read_other_tenant_policies(two_tenants: TwoTenants) -> None:
    t1, m1, _t2, m2 = two_tenants
    await _seed_policy(m1.id)
    await _seed_policy(m2.id)

    async with tenant_session(_admin_ctx(t1.id)) as session:
        repo = StorePolicyRepository(session)
        assert await repo.get_for_merchant(m1.id) is not None
        assert await repo.get_for_merchant(m2.id) is None

        rows = (await session.execute(select(StorePolicy))).scalars().all()
        assert all(r.merchant_id == m1.id for r in rows)


def _admin_ctx(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        merchant_id=None,
        role="agency_admin",
        actor_id=uuid.uuid4(),
    )
