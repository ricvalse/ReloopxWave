"""RLS per profili di conversazione, definizioni di esito e fatti (migrazione 0047).

Tre tabelle nuove, due forme di policy:

- `conversation_profiles` e `outcome_definitions` hanno `merchant_id` **nullable**
  (NULL = riga di libreria d'agenzia). Il predicato merchant-scoped standard le
  renderebbe invisibili — con `merchant_id` NULL l'EXISTS su `merchants` è falso
  — quindi usano la forma tenant-OR-merchant con USING e WITH CHECK asimmetrici:
  un merchant **legge** anche la libreria ma **scrive** solo righe intestate a sé.
- `lead_outcomes` ha `merchant_id` NOT NULL → predicato merchant-scoped classico.

L'ultimo test copre l'invariante che rende corretti i conteggi: l'indice unique
parziale su `cardinality='once_per_lead'` fa sì che un secondo `record()` per lo
stesso lead sia un no-op invece di un duplicato. È l'idempotenza su cui si regge
un motore di automazioni stateless che ri-esegue lo stesso ramo a ogni inbound.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select

from db import OutcomeRepository, TenantContext, session_scope, tenant_session
from db.models import ConversationProfile, Lead, LeadOutcome, Merchant, OutcomeDefinition, Tenant

pytestmark = pytest.mark.asyncio

Seeded = tuple[uuid.UUID, uuid.UUID, uuid.UUID]


@pytest_asyncio.fixture
async def seeded() -> AsyncIterator[Seeded]:
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

        session.add_all(
            [
                # Un profilo per merchant + uno di libreria d'agenzia.
                ConversationProfile(
                    tenant_id=t_id, merchant_id=m1_id, key="reception", name="Reception"
                ),
                ConversationProfile(
                    tenant_id=t_id, merchant_id=m2_id, key="reception", name="Reception"
                ),
                ConversationProfile(
                    tenant_id=t_id, merchant_id=None, key="consulenza", name="Consulenza"
                ),
                OutcomeDefinition(
                    tenant_id=t_id,
                    merchant_id=m1_id,
                    key="questionario_compilato",
                    label="Ha compilato il questionario",
                ),
                OutcomeDefinition(
                    tenant_id=t_id,
                    merchant_id=m2_id,
                    key="questionario_compilato",
                    label="Ha compilato il questionario",
                ),
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
        tenant_id=tenant_id, merchant_id=merchant_id, role="merchant_admin", actor_id=uuid.uuid4()
    )


def _agency_ctx(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id, merchant_id=None, role="agency_admin", actor_id=uuid.uuid4()
    )


async def test_merchant_sees_own_profiles_and_the_agency_library(seeded: Seeded) -> None:
    t_id, m1_id, m2_id = seeded

    async with tenant_session(_merchant_ctx(t_id, m1_id)) as session:
        rows = (await session.execute(select(ConversationProfile))).scalars().all()
        owners = {r.merchant_id for r in rows}

    assert m1_id in owners
    assert m2_id not in owners
    # La libreria d'agenzia (merchant_id NULL) deve restare visibile: è quella
    # che il merchant adotta.
    assert None in owners


async def test_agency_sees_every_profile_of_the_tenant(seeded: Seeded) -> None:
    t_id, m1_id, m2_id = seeded

    async with tenant_session(_agency_ctx(t_id)) as session:
        rows = (await session.execute(select(ConversationProfile))).scalars().all()
        owners = {r.merchant_id for r in rows}

    assert {m1_id, m2_id, None} <= owners


async def test_merchant_sees_only_own_outcome_definitions(seeded: Seeded) -> None:
    t_id, m1_id, m2_id = seeded

    async with tenant_session(_merchant_ctx(t_id, m1_id)) as session:
        rows = (await session.execute(select(OutcomeDefinition))).scalars().all()
        owners = {r.merchant_id for r in rows}

    assert m1_id in owners
    assert m2_id not in owners


async def test_merchant_cannot_write_an_agency_library_row(seeded: Seeded) -> None:
    """WITH CHECK asimmetrico: leggere la libreria sì, scriverla no."""
    t_id, m1_id, _ = seeded

    with pytest.raises(Exception):  # noqa: B017 — RLS violation, driver-specific
        async with tenant_session(_merchant_ctx(t_id, m1_id)) as session:
            session.add(
                ConversationProfile(tenant_id=t_id, merchant_id=None, key="abusivo", name="Abusivo")
            )
            await session.flush()


async def test_merchant_sees_only_own_lead_outcomes(seeded: Seeded) -> None:
    t_id, m1_id, m2_id = seeded

    async with session_scope() as session:
        defs = (await session.execute(select(OutcomeDefinition))).scalars().all()
        by_merchant = {d.merchant_id: d for d in defs}
        leads = [
            Lead(merchant_id=m1_id, phone=f"39{uuid.uuid4().int % 10**10:010d}"),
            Lead(merchant_id=m2_id, phone=f"39{uuid.uuid4().int % 10**10:010d}"),
        ]
        session.add_all(leads)
        await session.flush()
        session.add_all(
            [
                LeadOutcome(
                    tenant_id=t_id,
                    merchant_id=m1_id,
                    outcome_id=by_merchant[m1_id].id,
                    lead_id=leads[0].id,
                    source="ai_check",
                    cardinality="once_per_lead",
                ),
                LeadOutcome(
                    tenant_id=t_id,
                    merchant_id=m2_id,
                    outcome_id=by_merchant[m2_id].id,
                    lead_id=leads[1].id,
                    source="ai_check",
                    cardinality="once_per_lead",
                ),
            ]
        )

    async with tenant_session(_merchant_ctx(t_id, m1_id)) as session:
        rows = (await session.execute(select(LeadOutcome))).scalars().all()
        owners = {r.merchant_id for r in rows}

    assert m1_id in owners
    assert m2_id not in owners


async def test_recording_the_same_outcome_twice_is_a_no_op(seeded: Seeded) -> None:
    """L'idempotenza sta nell'indice unique, non nella logica applicativa.

    Il motore delle automazioni è stateless e ri-esegue lo stesso ramo a ogni
    inbound, con la conferma del lead ancora dentro la finestra di storico letta
    dall'`ai_check`: senza questo vincolo il conteggio crescerebbe a ogni
    messaggio successivo.
    """
    t_id, m1_id, _ = seeded

    async with session_scope() as session:
        definition = (
            (
                await session.execute(
                    select(OutcomeDefinition).where(OutcomeDefinition.merchant_id == m1_id)
                )
            )
            .scalars()
            .one()
        )
        lead = Lead(merchant_id=m1_id, phone=f"39{uuid.uuid4().int % 10**10:010d}")
        session.add(lead)
        await session.flush()

        repo = OutcomeRepository(session)
        first = await repo.record(
            tenant_id=t_id,
            merchant_id=m1_id,
            outcome_id=definition.id,
            lead_id=lead.id,
            cardinality="once_per_lead",
            source="ai_check",
        )
        second = await repo.record(
            tenant_id=t_id,
            merchant_id=m1_id,
            outcome_id=definition.id,
            lead_id=lead.id,
            cardinality="once_per_lead",
            source="ai_check",
        )
        rows = (
            (
                await session.execute(
                    select(LeadOutcome).where(LeadOutcome.outcome_id == definition.id)
                )
            )
            .scalars()
            .all()
        )

    assert first is True
    assert second is False
    assert len(rows) == 1
