"""Cross-tenant RLS isolation for the merchant-content tables (store_policies /
faq_entries / bot_corrections). Mirrors test_isolation_templates.py: two
JWT-shaped contexts against a real Postgres, asserting tenant A cannot observe
tenant B's rows. Auto-skips when SUPABASE_DB_URL is unset.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from db import (
    BotCorrectionRepository,
    FaqRepository,
    StorePolicyRepository,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.models import BotCorrection, FaqEntry, Merchant, StorePolicy, Tenant

pytestmark = pytest.mark.asyncio

TwoTenants = tuple[Tenant, Merchant, Tenant, Merchant]


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


async def _seed_correction(merchant_id: uuid.UUID, trigger: str) -> uuid.UUID:
    async with session_scope() as session:
        row = BotCorrection(
            merchant_id=merchant_id,
            trigger_message=trigger,
            original_response="risposta sbagliata",
            corrected_response="risposta corretta",
        )
        session.add(row)
        await session.flush()
        return row.id


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


async def test_tenant_cannot_read_other_tenant_corrections(two_tenants: TwoTenants) -> None:
    t1, m1, _t2, m2 = two_tenants
    await _seed_correction(m1.id, "scarpe blu")
    foreign_id = await _seed_correction(m2.id, "orari")

    async with tenant_session(_admin_ctx(t1.id)) as session:
        repo = BotCorrectionRepository(session)
        assert len(await repo.list_for_merchant(m1.id)) == 1
        assert await repo.list_for_merchant(m2.id) == []
        assert await repo.get(foreign_id) is None

        rows = (await session.execute(select(BotCorrection))).scalars().all()
        assert all(r.merchant_id == m1.id for r in rows)


def _admin_ctx(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        merchant_id=None,
        role="agency_admin",
        actor_id=uuid.uuid4(),
    )
