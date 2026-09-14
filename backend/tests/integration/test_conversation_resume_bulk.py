"""`ConversationRepository.resume_paused_bulk` — the inbox "Riattiva tutte" action.

Exercises the one property that matters here: a bulk click must clear every
soft-paused thread (phone-echo pause, timed "disattiva AI") but never touch a
thread with a real handoff record, since nobody reviewed those individually
before the click. Auto-skipped when SUPABASE_DB_URL is unset.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from db import ConversationRepository, TenantContext, session_scope, tenant_session
from db.models import Conversation, Lead, Merchant, Tenant

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def seeded_data() -> AsyncIterator[dict[str, Any]]:
    """One tenant, one merchant, three conversations in three different states."""
    suffix = uuid.uuid4().hex[:8]
    future = datetime.now(UTC) + timedelta(hours=2)
    past = datetime.now(UTC) - timedelta(minutes=5)

    async with session_scope() as session:
        tenant = Tenant(slug=f"resume-t-{suffix}", name=f"Resume T {suffix}")
        session.add(tenant)
        await session.flush()

        merchant = Merchant(
            tenant_id=tenant.id, slug=f"resume-m-{suffix}", name=f"Resume M {suffix}"
        )
        other_merchant = Merchant(
            tenant_id=tenant.id, slug=f"resume-m2-{suffix}", name=f"Resume M2 {suffix}"
        )
        session.add_all([merchant, other_merchant])
        await session.flush()

        lead_paused = Lead(merchant_id=merchant.id, phone=f"39444{suffix[:6]}1")
        lead_handoff = Lead(merchant_id=merchant.id, phone=f"39444{suffix[:6]}2")
        lead_expired = Lead(merchant_id=merchant.id, phone=f"39444{suffix[:6]}3")
        lead_other = Lead(merchant_id=other_merchant.id, phone=f"39444{suffix[:6]}4")
        session.add_all([lead_paused, lead_handoff, lead_expired, lead_other])
        await session.flush()

        # Soft-paused, no handoff — this is the one a bulk click should fix.
        conv_paused = Conversation(
            merchant_id=merchant.id,
            lead_id=lead_paused.id,
            wa_contact_phone=lead_paused.phone,
            ai_disabled_until=future,
        )
        # Soft-paused AND a real open handoff — must survive the bulk click
        # untouched; only the single-conversation resume may clear this.
        conv_handoff = Conversation(
            merchant_id=merchant.id,
            lead_id=lead_handoff.id,
            wa_contact_phone=lead_handoff.phone,
            ai_disabled_until=future,
            auto_reply=False,
            handoff_at=datetime.now(UTC),
            handoff_reason="escalate_human",
        )
        # Pause already expired — nothing to do, not "stuck".
        conv_expired = Conversation(
            merchant_id=merchant.id,
            lead_id=lead_expired.id,
            wa_contact_phone=lead_expired.phone,
            ai_disabled_until=past,
        )
        # Paused, but belongs to a different merchant in the same tenant.
        conv_other = Conversation(
            merchant_id=other_merchant.id,
            lead_id=lead_other.id,
            wa_contact_phone=lead_other.phone,
            ai_disabled_until=future,
        )
        session.add_all([conv_paused, conv_handoff, conv_expired, conv_other])
        await session.flush()

        snapshot = {
            "tenant_id": tenant.id,
            "merchant_id": merchant.id,
            "other_merchant_id": other_merchant.id,
            "conv_paused_id": conv_paused.id,
            "conv_handoff_id": conv_handoff.id,
            "conv_expired_id": conv_expired.id,
            "conv_other_id": conv_other.id,
        }

    try:
        yield snapshot
    finally:
        async with session_scope() as session:
            tenant = await session.get(Tenant, snapshot["tenant_id"])
            if tenant is not None:
                await session.delete(tenant)


def _ctx(tenant_id: uuid.UUID, merchant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        role="merchant_admin",
        actor_id=uuid.uuid4(),
    )


async def test_resume_paused_bulk_clears_only_pure_soft_pause(
    seeded_data: dict[str, Any],
) -> None:
    ctx = _ctx(seeded_data["tenant_id"], seeded_data["merchant_id"])
    async with tenant_session(ctx) as session:
        resumed = await ConversationRepository(session).resume_paused_bulk(
            seeded_data["merchant_id"]
        )

    assert resumed == [seeded_data["conv_paused_id"]]

    async with tenant_session(ctx) as session:
        paused = await session.get(Conversation, seeded_data["conv_paused_id"])
        assert paused is not None
        assert paused.ai_disabled_until is None
        assert paused.auto_reply is True

        # Untouched: a real handoff needs a human to look at it first.
        handoff = await session.get(Conversation, seeded_data["conv_handoff_id"])
        assert handoff is not None
        assert handoff.ai_disabled_until is not None
        assert handoff.auto_reply is False
        assert handoff.handoff_resolved_at is None

        # Untouched: already expired, wasn't "stuck" to begin with.
        expired = await session.get(Conversation, seeded_data["conv_expired_id"])
        assert expired is not None
        assert expired.ai_disabled_until is not None


async def test_resume_paused_bulk_does_not_cross_merchants(seeded_data: dict[str, Any]) -> None:
    ctx = _ctx(seeded_data["tenant_id"], seeded_data["merchant_id"])
    async with tenant_session(ctx) as session:
        await ConversationRepository(session).resume_paused_bulk(seeded_data["merchant_id"])

    other_ctx = _ctx(seeded_data["tenant_id"], seeded_data["other_merchant_id"])
    async with tenant_session(other_ctx) as session:
        other = await session.get(Conversation, seeded_data["conv_other_id"])
        assert other is not None
        assert other.ai_disabled_until is not None
