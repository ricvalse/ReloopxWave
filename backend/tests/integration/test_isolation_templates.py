"""Cross-tenant RLS isolation for the WhatsApp template + flow tables.

Mirrors test_isolation.py: two JWT-shaped contexts against a real Postgres,
asserting tenant A cannot observe tenant B's whatsapp_templates / flows /
flow_steps rows. Auto-skips when SUPABASE_DB_URL is unset.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from db import (
    FlowRepository,
    TenantContext,
    WhatsAppTemplateRepository,
    session_scope,
    tenant_session,
)
from db.models import Flow, FlowStep, Merchant, Tenant, WhatsAppTemplate

pytestmark = pytest.mark.asyncio

TwoTenants = tuple[Tenant, Merchant, Tenant, Merchant]


async def _seed_template(merchant_id: uuid.UUID, name: str) -> uuid.UUID:
    async with session_scope() as session:
        tpl = WhatsAppTemplate(
            merchant_id=merchant_id,
            name=name,
            category="UTILITY",
            language="it",
            purpose="reactivation",
            body="Ciao, possiamo riprendere?",
            variables=[],
            variable_sources={},
            status="approved",
        )
        session.add(tpl)
        await session.flush()
        return tpl.id


async def _seed_flow(merchant_id: uuid.UUID, key: str) -> uuid.UUID:
    async with session_scope() as session:
        flow = Flow(merchant_id=merchant_id, key=key, name=f"flow-{key}")
        session.add(flow)
        await session.flush()
        step = FlowStep(
            flow_id=flow.id,
            merchant_id=merchant_id,
            step_index=0,
            delay_minutes=0,
            window_policy="auto",
        )
        session.add(step)
        await session.flush()
        return flow.id


async def test_tenant_cannot_read_other_tenant_templates(two_tenants: TwoTenants) -> None:
    t1, m1, _t2, m2 = two_tenants
    await _seed_template(m1.id, f"tpl_a_{uuid.uuid4().hex[:8]}")
    foreign_id = await _seed_template(m2.id, f"tpl_b_{uuid.uuid4().hex[:8]}")

    async with tenant_session(_admin_ctx(t1.id)) as session:
        repo = WhatsAppTemplateRepository(session)
        mine = await repo.list_for_merchant(m1.id)
        assert len(mine) == 1

        # Foreign merchant's templates are invisible even when queried directly.
        foreign_list = await repo.list_for_merchant(m2.id)
        assert foreign_list == []

        # Raw select is filtered to this tenant only.
        rows = (await session.execute(select(WhatsAppTemplate))).scalars().all()
        assert all(r.merchant_id == m1.id for r in rows)

        # PK lookup for the foreign row returns None under FORCE RLS.
        assert await repo.get(foreign_id) is None


async def test_tenant_cannot_read_other_tenant_flows(two_tenants: TwoTenants) -> None:
    t1, m1, _t2, m2 = two_tenants
    await _seed_flow(m1.id, "reactivation")
    await _seed_flow(m2.id, "reactivation")

    async with tenant_session(_admin_ctx(t1.id)) as session:
        mine = await FlowRepository(session).list_for_merchant(m1.id)
        assert len(mine) == 1

        foreign = await FlowRepository(session).list_for_merchant(m2.id)
        assert foreign == []

        flow_rows = (await session.execute(select(Flow))).scalars().all()
        assert all(f.merchant_id == m1.id for f in flow_rows)

        step_rows = (await session.execute(select(FlowStep))).scalars().all()
        assert all(s.merchant_id == m1.id for s in step_rows)


def _admin_ctx(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        merchant_id=None,
        role="agency_admin",
        actor_id=uuid.uuid4(),
    )
