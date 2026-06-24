"""Parametric cross-tenant RLS isolation across every merchant-scoped table.

One generic test asserts the core invariant for each table: a row seeded for
tenant A's merchant is invisible to (a) tenant B's context and (b) the
non-owning merchant's context. Complements the per-table suites
(test_isolation_catalog/templates/...) with broad coverage so a new table that
forgets its policy is caught by a single parametrisation.

Each table provides a `seed(merchant_id) -> None` factory and the ORM model to
SELECT under the foreign contexts. Tables needing a parent row (objections need
a conversation, ab_assignments need an experiment + lead, kb_chunks need a doc)
seed the parent in the same `session_scope` (superuser bypasses RLS while
seeding). The raw SELECT runs under the non-bypassrls `authenticated` role that
`tenant_session` downgrades to, so RLS is actually enforced.

Auto-skips when SUPABASE_DB_URL is unset.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import func, select

from db import TenantContext, session_scope, tenant_session
from db.models import (
    ABAssignment,
    ABExperiment,
    BotConfig,
    Conversation,
    FTModel,
    Integration,
    KBChunk,
    KnowledgeBaseDoc,
    Lead,
    Merchant,
    Objection,
    Product,
    PromptTemplate,
    Tenant,
    WhatsAppTemplate,
)
from db.models.kb import EMBEDDING_DIM

pytestmark = pytest.mark.asyncio

TwoTenants = tuple[Tenant, Merchant, Tenant, Merchant]

Seeder = Callable[[uuid.UUID], Awaitable[None]]


# --------------------------------------------------------------------------- #
# Per-table seed factories — each inserts one minimal valid row for `merchant`.
# --------------------------------------------------------------------------- #


async def _seed_lead(merchant_id: uuid.UUID) -> None:
    async with session_scope() as session:
        session.add(
            Lead(
                merchant_id=merchant_id,
                phone=f"39{uuid.uuid4().int % 10**9:09d}",
                campaign="estate-2026",  # exercises the lead_campaign column too
            )
        )
        await session.flush()


async def _seed_opted_out_lead(merchant_id: uuid.UUID) -> None:
    """A lead with opted_out_at set — the `leads_opted_out` view of the table."""
    from datetime import UTC, datetime

    async with session_scope() as session:
        session.add(
            Lead(
                merchant_id=merchant_id,
                phone=f"39{uuid.uuid4().int % 10**9:09d}",
                opted_out_at=datetime.now(tz=UTC),
            )
        )
        await session.flush()


async def _seed_product(merchant_id: uuid.UUID) -> None:
    async with session_scope() as session:
        session.add(
            Product(
                merchant_id=merchant_id,
                title="Prodotto",
                handle=f"p-{uuid.uuid4().hex[:8]}",
                tags=[],
                variants=[],
                images=[],
            )
        )
        await session.flush()


async def _seed_prompt_template(merchant_id: uuid.UUID) -> None:
    async with session_scope() as session:
        session.add(
            PromptTemplate(
                merchant_id=merchant_id,
                kind="system",
                body="sei un assistente",
            )
        )
        await session.flush()


async def _seed_bot_config(merchant_id: uuid.UUID) -> None:
    async with session_scope() as session:
        session.add(BotConfig(merchant_id=merchant_id, overrides={"tone": "friendly"}))
        await session.flush()


async def _seed_integration(merchant_id: uuid.UUID) -> None:
    async with session_scope() as session:
        session.add(
            Integration(
                merchant_id=merchant_id,
                provider="whatsapp",
                status="active",
                secret_ciphertext=b"\x00",
                secret_nonce=b"\x00",
            )
        )
        await session.flush()


async def _seed_ft_model(merchant_id: uuid.UUID) -> None:
    async with session_scope() as session:
        # ft_models also carry tenant_id (nullable merchant); set both.
        merchant = await session.get(Merchant, merchant_id)
        assert merchant is not None
        session.add(
            FTModel(
                tenant_id=merchant.tenant_id,
                merchant_id=merchant_id,
                version=1,
                base_model="gpt-4.1-mini",
                provider_model_id="ft:fake",
            )
        )
        await session.flush()


async def _seed_whatsapp_template(merchant_id: uuid.UUID) -> None:
    async with session_scope() as session:
        session.add(
            WhatsAppTemplate(
                merchant_id=merchant_id,
                name=f"tpl_{uuid.uuid4().hex[:8]}",
                body="Ciao {{1}}",
            )
        )
        await session.flush()


async def _seed_knowledge_base_doc(merchant_id: uuid.UUID) -> None:
    async with session_scope() as session:
        session.add(
            KnowledgeBaseDoc(
                merchant_id=merchant_id,
                title="Doc",
                source="txt",
            )
        )
        await session.flush()


async def _seed_kb_chunk(merchant_id: uuid.UUID) -> None:
    async with session_scope() as session:
        doc = KnowledgeBaseDoc(merchant_id=merchant_id, title="Doc", source="txt")
        session.add(doc)
        await session.flush()
        session.add(
            KBChunk(
                doc_id=doc.id,
                merchant_id=merchant_id,
                chunk_index=0,
                content="contenuto",
                embedding=[0.0] * EMBEDDING_DIM,
            )
        )
        await session.flush()


async def _seed_objection(merchant_id: uuid.UUID) -> None:
    async with session_scope() as session:
        conv = Conversation(merchant_id=merchant_id, status="active")
        session.add(conv)
        await session.flush()
        session.add(
            Objection(
                merchant_id=merchant_id,
                conversation_id=conv.id,
                category="prezzo",
                summary="troppo caro",
            )
        )
        await session.flush()


async def _seed_ab_experiment(merchant_id: uuid.UUID) -> None:
    async with session_scope() as session:
        session.add(
            ABExperiment(
                merchant_id=merchant_id,
                name="esperimento",
                variants=[{"id": "A"}, {"id": "B"}],
                primary_metric="booking_rate",
            )
        )
        await session.flush()


async def _seed_ab_assignment(merchant_id: uuid.UUID) -> None:
    async with session_scope() as session:
        exp = ABExperiment(
            merchant_id=merchant_id,
            name="esperimento",
            variants=[{"id": "A"}, {"id": "B"}],
            primary_metric="booking_rate",
        )
        lead = Lead(merchant_id=merchant_id, phone=f"39{uuid.uuid4().int % 10**9:09d}")
        session.add_all([exp, lead])
        await session.flush()
        session.add(
            ABAssignment(
                experiment_id=exp.id,
                merchant_id=merchant_id,
                lead_id=lead.id,
                variant_id="A",
            )
        )
        await session.flush()


# (label, model, seeder) — label feeds pytest ids; model is SELECTed under the
# foreign contexts. Labels include the conceptual names from the audit
# (lead_campaign / leads_opted_out are facets of the `leads` table).
_CASES: list[tuple[str, type[Any], Seeder]] = [
    ("lead_campaign", Lead, _seed_lead),
    ("leads_opted_out", Lead, _seed_opted_out_lead),
    ("products", Product, _seed_product),
    ("prompt_templates", PromptTemplate, _seed_prompt_template),
    ("bot_configs", BotConfig, _seed_bot_config),
    ("integrations", Integration, _seed_integration),
    ("ft_models", FTModel, _seed_ft_model),
    ("whatsapp_templates", WhatsAppTemplate, _seed_whatsapp_template),
    ("knowledge_base_docs", KnowledgeBaseDoc, _seed_knowledge_base_doc),
    ("kb_chunks", KBChunk, _seed_kb_chunk),
    ("objections", Objection, _seed_objection),
    ("ab_experiments", ABExperiment, _seed_ab_experiment),
    ("ab_assignments", ABAssignment, _seed_ab_assignment),
]


@pytest.mark.parametrize(
    ("label", "model", "seed"),
    _CASES,
    ids=[c[0] for c in _CASES],
)
async def test_merchant_scoped_table_is_isolated(
    two_tenants: TwoTenants,
    label: str,
    model: type[Any],
    seed: Seeder,
) -> None:
    t1, m1, t2, m2 = two_tenants
    await seed(m1.id)

    own_count = _count_stmt(model, m1.id)
    foreign_count = _count_stmt(model, m1.id)

    # Owner (tenant A, merchant A) sees its row.
    async with tenant_session(_merchant_ctx(t1.id, m1.id)) as session:
        assert (await session.execute(own_count)).scalar_one() >= 1, (
            f"{label}: owner must see its own row"
        )

    # Tenant B (different tenant entirely) sees zero rows of the table.
    async with tenant_session(_admin_ctx(t2.id)) as session:
        total_for_tenant_b = (
            await session.execute(select(func.count()).select_from(model))
        ).scalar_one()
        assert total_for_tenant_b == 0, f"{label}: tenant B leaked {total_for_tenant_b} rows"

    # Non-owning merchant under the SAME-shaped role but a foreign merchant_id
    # claim (merchant B) must also see zero rows scoped to merchant A.
    async with tenant_session(_merchant_ctx(t2.id, m2.id)) as session:
        assert (await session.execute(foreign_count)).scalar_one() == 0, (
            f"{label}: non-owning merchant saw merchant A's row"
        )


def _count_stmt(model: type[Any], merchant_id: uuid.UUID) -> Any:
    return select(func.count()).select_from(model).where(model.merchant_id == merchant_id)


def _admin_ctx(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        merchant_id=None,
        role="agency_admin",
        actor_id=uuid.uuid4(),
    )


def _merchant_ctx(tenant_id: uuid.UUID, merchant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        role="merchant_user",
        actor_id=uuid.uuid4(),
    )
