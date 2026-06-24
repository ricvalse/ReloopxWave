"""Cross-tenant RLS isolation for the automation tables (automation_flows /
automation_nodes / automation_edges — the visual "lavagnetta"). Mirrors
test_isolation_catalog.py: two JWT-shaped contexts against a real Postgres,
asserting tenant A cannot observe (or write) tenant B's automation graph.

Covers the denormalised `merchant_id` on nodes/edges explicitly: a raw SELECT
under the non-bypassrls `authenticated` role must return zero foreign rows even
when querying the child tables directly (not just through the parent flow).

Auto-skips when SUPABASE_DB_URL is unset.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from db import (
    AutomationRepository,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.models import (
    AutomationEdge,
    AutomationFlow,
    AutomationNode,
    Merchant,
    Tenant,
)

pytestmark = pytest.mark.asyncio

TwoTenants = tuple[Tenant, Merchant, Tenant, Merchant]


async def _seed_automation(merchant_id: uuid.UUID, name: str) -> uuid.UUID:
    """Seed a minimal trigger→send graph for a merchant; return the flow id."""
    async with session_scope() as session:
        repo = AutomationRepository(session)
        flow = await repo.create(
            merchant_id=merchant_id,
            name=name,
            enabled=True,
            trigger_type="message_received",
            trigger_config={},
        )
        await repo.replace_graph(
            flow,
            nodes=[
                {
                    "node_key": "t1",
                    "kind": "trigger",
                    "type": "message_received",
                    "config": {},
                },
                {
                    "node_key": "a1",
                    "kind": "action",
                    "type": "send_message",
                    "config": {"text": "ciao"},
                },
            ],
            edges=[{"source_key": "t1", "target_key": "a1", "branch": "default"}],
        )
        return flow.id


async def test_tenant_cannot_read_other_tenant_automation_flows(
    two_tenants: TwoTenants,
) -> None:
    t1, m1, _t2, m2 = two_tenants
    await _seed_automation(m1.id, "Mia automazione")
    foreign_id = await _seed_automation(m2.id, "Automazione altrui")

    async with tenant_session(_admin_ctx(t1.id)) as session:
        repo = AutomationRepository(session)
        assert len(await repo.list_for_merchant(m1.id)) == 1
        assert await repo.list_for_merchant(m2.id) == []
        # PK lookup for the foreign flow returns None under FORCE RLS.
        assert await repo.get(foreign_id) is None

        rows = (await session.execute(select(AutomationFlow))).scalars().all()
        assert all(r.merchant_id == m1.id for r in rows)


async def test_tenant_cannot_read_other_tenant_automation_nodes(
    two_tenants: TwoTenants,
) -> None:
    """The denormalised merchant_id on nodes must scope a *direct* child query."""
    t1, m1, _t2, m2 = two_tenants
    await _seed_automation(m1.id, "Mia automazione")
    await _seed_automation(m2.id, "Automazione altrui")

    async with tenant_session(_admin_ctx(t1.id)) as session:
        rows = (await session.execute(select(AutomationNode))).scalars().all()
        # Both flows seeded 2 nodes each; tenant A must see only its own.
        assert rows  # sanity: own nodes are visible
        assert all(r.merchant_id == m1.id for r in rows)


async def test_tenant_cannot_read_other_tenant_automation_edges(
    two_tenants: TwoTenants,
) -> None:
    """Same for edges — the denormalised merchant_id is the RLS predicate."""
    t1, m1, _t2, m2 = two_tenants
    await _seed_automation(m1.id, "Mia automazione")
    await _seed_automation(m2.id, "Automazione altrui")

    async with tenant_session(_admin_ctx(t1.id)) as session:
        rows = (await session.execute(select(AutomationEdge))).scalars().all()
        assert rows
        assert all(r.merchant_id == m1.id for r in rows)


async def test_tenant_cannot_write_automation_for_other_merchant(
    two_tenants: TwoTenants,
) -> None:
    """An INSERT carrying a foreign merchant_id is rejected by the WITH CHECK
    side of the RLS policy (the row is invisible / refused)."""
    t1, _m1, _t2, m2 = two_tenants

    async with tenant_session(_admin_ctx(t1.id)) as session:
        session.add(
            AutomationFlow(
                merchant_id=m2.id,  # foreign merchant — must be refused
                name="injection",
                enabled=False,
                trigger_config={},
                canvas={},
            )
        )
        # RLS WITH CHECK rejects the foreign-merchant insert at flush time.
        with pytest.raises(DBAPIError):
            await session.flush()


def _admin_ctx(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        merchant_id=None,
        role="agency_admin",
        actor_id=uuid.uuid4(),
    )
