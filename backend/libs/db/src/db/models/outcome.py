"""Esiti tracciabili: il vocabolario (`OutcomeDefinition`) e i fatti (`LeadOutcome`).

Le metriche "messaggi inviati" e "risposte ricevute" sono strutturali: il loro
vocabolario sta nel codice (`direction`, `sender_type`, `automation_id` esistono
per ogni merchant). Un esito come "ha compilato il questionario" no — quel
vocabolario appartiene al merchant, e un builder di metriche ha bisogno di una
tendina da cui sceglierlo.

Se l'esito restasse una stringa libera si riaprirebbe esattamente il bug che ha
motivato il catalogo eventi tipato di ADR 0021: un emettitore scrive
`questionario_compilto`, il lettore cerca `questionario_compilato`, la bolla
resta a zero e nessuno se ne accorge. Per questo `LeadOutcome.outcome_id` è una
**FK** a `OutcomeDefinition`, e il nodo `emit_outcome` della lavagnetta sceglie
da una tendina invece di far digitare una stringa.

L'idempotenza è nel database, non nella logica applicativa: il motore delle
automazioni è stateless (ADR 0015) e ri-esegue lo stesso ramo a ogni inbound, con
la conferma del lead ancora presente nella finestra di storico letta
dall'`ai_check`. Gli indici unique parziali su `cardinality` fanno sì che la
seconda emissione sia un no-op (`ON CONFLICT DO NOTHING`) invece di un duplicato
— stesso principio del claim atomico dell'handoff (ADR 0017).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, uuid_pk

# Come è stato accertato un esito. Sta sulla riga (non sulla definizione) così si
# può partire con `ai_check` e passare a un webhook più avanti senza perdere lo
# storico né mescolare in silenzio dati di qualità diversa.
OUTCOME_SOURCES = ("ai_check", "webhook", "manual", "automation", "agent_action", "api")

# Quante volte lo stesso esito può ripetersi per lo stesso soggetto.
# `once_per_lead` è il default e il caso del questionario: un COUNT(*) conta lead
# distinti senza bisogno di DISTINCT.
OUTCOME_CARDINALITIES = ("once_per_lead", "once_per_conversation", "repeatable")


class OutcomeDefinition(Base, TimestampMixin):
    """Un esito che questo merchant (o l'agenzia, come libreria) sa tracciare."""

    __tablename__ = "outcome_definitions"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NULL = definizione-libreria d'agenzia (stessa ownership dei profili).
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Stabile: ci puntano le righe storiche, non si rinomina mai.
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    # Quello che il merchant vede: rinominabile a piacere senza orfanare niente.
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="ai_check")
    cardinality: Mapped[str] = mapped_column(String(24), nullable=False, default="once_per_lead")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class LeadOutcome(Base):
    """Il fatto: questo lead ha raggiunto questo esito, in questo momento.

    Append-only. La statistica è un COUNT su queste righe, mai un contatore
    incrementato: un contatore deriverebbe sotto le ri-esecuzioni del motore, non
    sarebbe affettabile per finestra temporale o per profilo, non sarebbe
    correggibile e non saprebbe dire *chi*.
    """

    __tablename__ = "lead_outcomes"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    outcome_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("outcome_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Timbri storici: sotto quale profilo e da quale automazione è maturato
    # l'esito. Nessuna FK di proposito — se il merchant cancella l'automazione,
    # la riga deve conservare da dove veniva invece di essere riscritta a NULL.
    profile_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    automation_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    automation_node_key: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    # Solo per le sorgenti inferite (ai_check). NULL per i fatti certi.
    confidence: Mapped[float | None] = mapped_column(Float)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Denormalizzata dalla definizione: un indice unique parziale non può leggere
    # una colonna di un'altra tabella.
    cardinality: Mapped[str] = mapped_column(String(24), nullable=False, default="once_per_lead")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
