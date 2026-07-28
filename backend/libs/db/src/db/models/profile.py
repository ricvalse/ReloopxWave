"""Profili di conversazione (ADR 0022).

Un profilo è un **DELTA** sulla persona base del merchant, selezionato
per-conversazione e caricabile da un'automazione o da una pipeline CRM. Il caso
d'uso: un parrucchiere ha di default il profilo "Reception" (assiste, capisce la
richiesta), ma esiste anche "Consulenza telefonica", caricato quando parte una
certa automazione, dove il bot sa di doversi comportare diversamente.

Distinto da:

- `BotConfig` — uno per merchant (`merchant_id` è UNIQUE), la config effettiva.
  Il profilo non la sostituisce: ci si sovrappone come livello 0 della cascata
  (profilo → merchant → agency → system).
- `BotTemplate` — i default d'agenzia, secondo livello della cascata.
- `ABExperiment.variants` — stocastici, uno solo "running", swap totale del
  prompt. I profili sono deterministici, hanno identità (`name`/`is_default`),
  convivono su conversazioni diverse e sono un delta. Sono **ortogonali** all'A/B
  e devono comporre: un profilo può ospitare un esperimento A/B sul suo prompt
  (ADR 0022, "Perché non generalizzare l'A/B").

`overrides` ha la stessa shape validata da `BotConfigSchema`. Lo scope V1 è il
solo comportamento — `conversation.playbook.*`, `bot.system_prompt_additions`, i
knob di tono/stile — più `dashboard.metrics`, che governa quali bolle mostra la
pagina Statistiche di quel profilo. Booking, scoring, RAG e `model_override`
restano fuori (ADR 0022 decisione 2).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, uuid_pk


class ConversationProfile(Base, TimestampMixin):
    __tablename__ = "conversation_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NULL = profilo-libreria d'agenzia, adottabile da qualunque merchant del
    # tenant. Unicità di `key` garantita da due indici parziali (per-merchant e
    # per-tenant sulla libreria), vedi migrazione 0047.
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Il profilo a cui una conversazione torna a fine episodio. Al massimo uno
    # per merchant (indice unique parziale).
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
