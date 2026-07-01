"""Seed a fully-configured demo merchant "Amalia" — brand, FAQ, policies.

Dev-only, idempotent. Run from `backend/`:

    uv run python scripts/seed_amalia.py

Refuses to run when ENVIRONMENT=production (a demo merchant must never leak into
prod). Re-running upserts by stable keys (tenant slug / merchant slug / FAQ
question). After seeding it runs the FAQ reindex inline so the demo bot can
immediately retrieve the FAQ (skipped if no OpenAI key).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select

from db import session_scope
from db.models import BotConfig, FaqEntry, Merchant, StorePolicy, Tenant
from shared import get_logger, get_settings

logger = get_logger(__name__)

TENANT_SLUG = "amalia-demo"
MERCHANT_SLUG = "amalia"

# "Friendly" tone preset (config_resolver.presets.TONE_PRESETS) + brand profile.
BOT_OVERRIDES: dict[str, Any] = {
    "business": {
        "name": "Amalia",
        "industry": "abbigliamento e accessori donna",
        "description": (
            "Boutique di moda femminile made in Italy, nata a Milano nel 2018. "
            "Capi sartoriali e accessori curati, tra eleganza quotidiana e occasioni speciali."
        ),
        "offer": "Tessuti 100% italiani, spedizione in 24/48h e reso gratuito entro 30 giorni.",
        "hours": "Lun-Sab 9:00-19:30",
        "location": "Milano — spedizioni in tutta Italia",
        "pricing_notes": "Fascia media; capi a partire da 39€. Sconto 10% dal secondo capo.",
        "website": "https://www.amalia.example",
    },
    "bot": {
        "formality": "dai-del-tu",
        "tone": "amichevole e caloroso",
        "verbosity": "equilibrato",
        "emoji_policy": "sobrio",
        "auto_reply_enabled": False,
        "first_message": "Ciao! Sono l'assistente di Amalia 😊 Come posso aiutarti?",
        "do_phrases": [
            "Usa un tono cordiale e disponibile",
            "Proponi un capo alternativo quando una taglia non è disponibile",
        ],
        "dont_phrases": [
            "Non offrire sconti non autorizzati",
            "Non dare informazioni su prodotti non presenti nel catalogo",
        ],
    },
}

FAQ: list[dict[str, Any]] = [
    {
        "question": "Quanto costa la spedizione?",
        "answer": "La spedizione è gratuita per ordini sopra i 49€. Sotto questa soglia il costo è di 5,90€.",
        "category": "Spedizioni",
    },
    {
        "question": "In quanto tempo arriva l'ordine?",
        "answer": "Consegna in 24/48h lavorative in tutta Italia con corriere espresso.",
        "category": "Spedizioni",
    },
    {
        "question": "Posso fare il reso?",
        "answer": "Sì, il reso è gratuito entro 30 giorni dall'acquisto. I capi devono essere integri e con cartellino.",
        "category": "Resi",
    },
    {
        "question": "Come faccio a sapere la mia taglia?",
        "answer": "Trovi la guida taglie nella scheda di ogni prodotto. Se hai dubbi, scrivici e ti aiutiamo a scegliere.",
        "category": "Taglie",
    },
    {
        "question": "Quali metodi di pagamento accettate?",
        "answer": "Carta di credito/debito, PayPal, Apple Pay, Google Pay e bonifico. Anche pagamento alla consegna.",
        "category": "Pagamenti",
    },
    {
        "question": "I capi sono made in Italy?",
        "answer": "Sì, la maggior parte della collezione è realizzata in Italia con tessuti italiani selezionati.",
        "category": "Prodotti",
    },
    {
        "question": "Posso cambiare la taglia di un capo già ordinato?",
        "answer": "Certo: avvia un reso gratuito e procedi con un nuovo ordine, oppure scrivici per il cambio diretto.",
        "category": "Resi",
    },
    {
        "question": "Avete un negozio fisico?",
        "answer": "Sì, siamo a Milano. Puoi anche prenotare un appuntamento per una consulenza di stile.",
        "category": "Negozio",
    },
    {
        "question": "Fate confezioni regalo?",
        "answer": "Sì, su richiesta aggiungiamo una confezione regalo elegante senza costi aggiuntivi.",
        "category": "Servizi",
    },
    {
        "question": "Come posso contattare l'assistenza?",
        "answer": "Scrivici su WhatsApp o via email a supporto@amalia.example, Lun-Sab 9:00-19:30.",
        "category": "Contatti",
    },
]

POLICY: dict[str, Any] = {
    "shipping_info": "Spedizione gratuita sopra 49€ (altrimenti 5,90€), consegna in 24/48h con corriere espresso.",
    "return_policy": "Reso gratuito entro 30 giorni. Capi integri, con cartellino e confezione originale.",
    "payment_methods": "Carta, PayPal, Apple Pay, Google Pay, bonifico e pagamento alla consegna.",
    "exchange_policy": "Cambio taglia/colore gratuito: avvia un reso e procedi con un nuovo ordine.",
    "warranty_info": "Garanzia legale di conformità di 24 mesi su difetti di fabbricazione.",
    "contact_info": "WhatsApp e supporto@amalia.example, Lun-Sab 9:00-19:30.",
    "custom_policies": [
        {
            "title": "Confezione regalo",
            "body": "Gratuita su richiesta, con biglietto personalizzato.",
        },
    ],
}


async def _upsert_tenant_merchant(session: Any) -> Merchant:
    tenant = (
        await session.execute(select(Tenant).where(Tenant.slug == TENANT_SLUG))
    ).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(slug=TENANT_SLUG, name="Amalia (demo)")
        session.add(tenant)
        await session.flush()
    merchant = (
        await session.execute(
            select(Merchant).where(Merchant.tenant_id == tenant.id, Merchant.slug == MERCHANT_SLUG)
        )
    ).scalar_one_or_none()
    if merchant is None:
        merchant = Merchant(tenant_id=tenant.id, slug=MERCHANT_SLUG, name="Amalia")
        session.add(merchant)
        await session.flush()
    return cast(Merchant, merchant)


async def _upsert_bot_config(session: Any, merchant_id: UUID) -> None:
    row = (
        await session.execute(select(BotConfig).where(BotConfig.merchant_id == merchant_id))
    ).scalar_one_or_none()
    if row is None:
        session.add(BotConfig(merchant_id=merchant_id, overrides=BOT_OVERRIDES))
    else:
        row.overrides = BOT_OVERRIDES
    await session.flush()


async def _upsert_faq(session: Any, merchant_id: UUID) -> int:
    for index, spec in enumerate(FAQ):
        existing = (
            await session.execute(
                select(FaqEntry).where(
                    FaqEntry.merchant_id == merchant_id,
                    FaqEntry.question == spec["question"],
                )
            )
        ).scalar_one_or_none()
        fields: dict[str, Any] = {
            "answer": spec["answer"],
            "category": spec.get("category"),
            "sort_order": index,
            "is_active": True,
        }
        if existing is None:
            session.add(FaqEntry(merchant_id=merchant_id, question=spec["question"], **fields))
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
    await session.flush()
    return len(FAQ)


async def _upsert_policy(session: Any, merchant_id: UUID) -> None:
    row = (
        await session.execute(select(StorePolicy).where(StorePolicy.merchant_id == merchant_id))
    ).scalar_one_or_none()
    if row is None:
        session.add(StorePolicy(merchant_id=merchant_id, **POLICY))
    else:
        for key, value in POLICY.items():
            setattr(row, key, value)
    await session.flush()


async def main() -> None:
    settings = get_settings()
    if settings.environment == "production":
        print("Refusing to seed the Amalia demo in production.", file=sys.stderr)
        sys.exit(1)

    async with session_scope() as session:
        merchant = await _upsert_tenant_merchant(session)
        merchant_id = merchant.id
        await _upsert_bot_config(session, merchant_id)
        n_faq = await _upsert_faq(session, merchant_id)
        await _upsert_policy(session, merchant_id)

    logger.info(
        "seed.amalia.upserted",
        merchant_id=str(merchant_id),
        faq=n_faq,
    )
    print(f"Seeded merchant 'amalia' ({merchant_id}): {n_faq} FAQ, policy.")

    # Index FAQ inline so the demo bot can retrieve it immediately.
    from workers.runtime import build_runtime
    from workers.scheduler.catalog_reindex import reindex_catalog

    result = await reindex_catalog(
        {"runtime": build_runtime(settings)}, merchant_id=str(merchant_id)
    )
    print(f"Reindex: {result}")


if __name__ == "__main__":
    asyncio.run(main())
