"""RLS coverage for conversations.internal_note (inbox detail-panel notes).

The PATCH /conversations/{id}/notes endpoint is a thin, RLS-scoped update of
this column (see routers/conversations.py::update_note) — the trust boundary
is the same `merchant_isolation_conversations` policy that protects the rest
of the row. These tests exercise that boundary at the DB level: an owner can
write and read back the note; a foreign tenant can neither read it nor reach
the row to update it. Auto-skipped when SUPABASE_DB_URL is unset.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select

from db import TenantContext, session_scope, tenant_session
from db.models import Conversation, Lead, Merchant, Tenant

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def seeded_data() -> AsyncIterator[dict[str, Any]]:
    """Two tenants, one merchant + lead + conversation each."""
    suffix = uuid.uuid4().hex[:8]
    async with session_scope() as session:
        t1 = Tenant(slug=f"note-t1-{suffix}", name=f"NT1 {suffix}")
        t2 = Tenant(slug=f"note-t2-{suffix}", name=f"NT2 {suffix}")
        session.add_all([t1, t2])
        await session.flush()

        m1 = Merchant(tenant_id=t1.id, slug=f"note-m1-{suffix}", name=f"NM1 {suffix}")
        m2 = Merchant(tenant_id=t2.id, slug=f"note-m2-{suffix}", name=f"NM2 {suffix}")
        session.add_all([m1, m2])
        await session.flush()

        l1 = Lead(merchant_id=m1.id, phone=f"39444{suffix[:6]}1")
        l2 = Lead(merchant_id=m2.id, phone=f"39444{suffix[:6]}2")
        session.add_all([l1, l2])
        await session.flush()

        c1 = Conversation(merchant_id=m1.id, lead_id=l1.id, wa_contact_phone=l1.phone)
        c2 = Conversation(merchant_id=m2.id, lead_id=l2.id, wa_contact_phone=l2.phone)
        session.add_all([c1, c2])
        await session.flush()

        snapshot = {
            "t1_id": t1.id,
            "t2_id": t2.id,
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


async def test_owner_can_set_and_read_note(seeded_data: dict[str, Any]) -> None:
    ctx = _ctx(seeded_data["t1_id"])
    async with tenant_session(ctx) as session:
        conv = await session.get(Conversation, seeded_data["c1_id"])
        assert conv is not None
        conv.internal_note = "Richiamare lunedì per il preventivo"

    async with tenant_session(ctx) as session:
        conv = await session.get(Conversation, seeded_data["c1_id"])
        assert conv is not None
        assert conv.internal_note == "Richiamare lunedì per il preventivo"


async def test_note_invisible_cross_tenant(seeded_data: dict[str, Any]) -> None:
    # Owner writes a note on its own conversation.
    async with tenant_session(_ctx(seeded_data["t1_id"])) as session:
        conv = await session.get(Conversation, seeded_data["c1_id"])
        assert conv is not None
        conv.internal_note = "secret note"

    # A foreign tenant cannot see the row at all (so cannot read the note,
    # and the endpoint's RLS-scoped lookup would 404).
    async with tenant_session(_ctx(seeded_data["t2_id"])) as session:
        assert await session.get(Conversation, seeded_data["c1_id"]) is None
        rows = (
            (
                await session.execute(
                    select(Conversation).where(Conversation.id == seeded_data["c1_id"])
                )
            )
            .scalars()
            .all()
        )
        assert rows == []
