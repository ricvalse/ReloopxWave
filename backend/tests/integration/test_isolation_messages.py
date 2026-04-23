"""Cross-tenant RLS isolation for merchant-scoped data tables.

Extends the baseline `test_isolation.py` with the high-value tables that
actually carry conversation data — if one of these leaks, the product's
privacy promise is dead. Messages and leads are the most sensitive rows; a
bug in the policy denormalisation path should fail here before prod.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from db import TenantContext, session_scope, tenant_session
from db.models import Conversation, Lead, Merchant, Message, Tenant

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def seeded_data() -> AsyncIterator[dict[str, Any]]:
    """Two tenants, one merchant each, with a lead + conversation + message on each."""
    suffix = uuid.uuid4().hex[:8]
    async with session_scope() as session:
        t1 = Tenant(slug=f"iso-t1-{suffix}", name=f"T1 {suffix}")
        t2 = Tenant(slug=f"iso-t2-{suffix}", name=f"T2 {suffix}")
        session.add_all([t1, t2])
        await session.flush()

        m1 = Merchant(tenant_id=t1.id, slug=f"iso-m1-{suffix}", name=f"M1 {suffix}")
        m2 = Merchant(tenant_id=t2.id, slug=f"iso-m2-{suffix}", name=f"M2 {suffix}")
        session.add_all([m1, m2])
        await session.flush()

        l1 = Lead(merchant_id=m1.id, phone=f"39333{suffix[:6]}1")
        l2 = Lead(merchant_id=m2.id, phone=f"39333{suffix[:6]}2")
        session.add_all([l1, l2])
        await session.flush()

        c1 = Conversation(merchant_id=m1.id, lead_id=l1.id, wa_contact_phone=l1.phone)
        c2 = Conversation(merchant_id=m2.id, lead_id=l2.id, wa_contact_phone=l2.phone)
        session.add_all([c1, c2])
        await session.flush()

        session.add_all(
            [
                Message(conversation_id=c1.id, merchant_id=m1.id, role="user", content="hi t1"),
                Message(conversation_id=c2.id, merchant_id=m2.id, role="user", content="hi t2"),
            ]
        )
        await session.flush()

        snapshot = {
            "t1_id": t1.id,
            "t2_id": t2.id,
            "m1_id": m1.id,
            "m2_id": m2.id,
            "l1_id": l1.id,
            "l2_id": l2.id,
            "c1_id": c1.id,
            "c2_id": c2.id,
        }

    try:
        yield snapshot
    finally:
        async with session_scope() as session:
            for tenant_id in (snapshot["t1_id"], snapshot["t2_id"]):
                tenant = await session.get(Tenant, tenant_id)
                if tenant is not None:
                    await session.delete(tenant)


def _ctx(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        merchant_id=None,
        role="agency_admin",
        actor_id=uuid.uuid4(),
    )


async def test_messages_invisible_cross_tenant(seeded_data: dict[str, Any]) -> None:
    ctx = _ctx(seeded_data["t1_id"])
    async with tenant_session(ctx) as session:
        foreign = await session.get(Message, seeded_data["c2_id"])
        assert foreign is None
        other_mid = seeded_data["m2_id"]
        from sqlalchemy import select

        rows = (
            (await session.execute(select(Message).where(Message.merchant_id == other_mid)))
            .scalars()
            .all()
        )
        assert rows == []


async def test_leads_invisible_cross_tenant(seeded_data: dict[str, Any]) -> None:
    ctx = _ctx(seeded_data["t1_id"])
    async with tenant_session(ctx) as session:
        foreign = await session.get(Lead, seeded_data["l2_id"])
        assert foreign is None


async def test_conversations_invisible_cross_tenant(seeded_data: dict[str, Any]) -> None:
    ctx = _ctx(seeded_data["t1_id"])
    async with tenant_session(ctx) as session:
        foreign = await session.get(Conversation, seeded_data["c2_id"])
        assert foreign is None


async def test_merchant_scoped_ctx_hides_sibling_merchant(seeded_data: dict[str, Any]) -> None:
    """A merchant_user JWT (tenant_id + merchant_id) must not see another
    merchant of its own tenant — even though they share the tenant.
    """
    # Add a sibling merchant under t1 so merchant_user-on-m1 must not see it.
    async with session_scope() as session:
        sibling = Merchant(
            tenant_id=seeded_data["t1_id"],
            slug=f"sibling-{uuid.uuid4().hex[:6]}",
            name="Sibling",
        )
        session.add(sibling)
        await session.flush()
        sibling_id = sibling.id

    try:
        merchant_ctx = TenantContext(
            tenant_id=seeded_data["t1_id"],
            merchant_id=seeded_data["m1_id"],
            role="merchant_user",
            actor_id=uuid.uuid4(),
        )
        async with tenant_session(merchant_ctx) as session:
            assert await session.get(Merchant, sibling_id) is None
    finally:
        async with session_scope() as session:
            row = await session.get(Merchant, sibling_id)
            if row is not None:
                await session.delete(row)
