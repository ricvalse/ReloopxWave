"""Repository degli esiti: vocabolario (`OutcomeDefinition`) e fatti (`LeadOutcome`).

`record()` è la sola porta di scrittura ed è idempotente per costruzione:
`INSERT ... ON CONFLICT DO NOTHING` contro gli indici unique parziali di 0047.
Serve perché il motore delle automazioni è stateless (ADR 0015) e ri-esegue lo
stesso ramo a ogni inbound: senza il vincolo, un lead che conferma una volta
produrrebbe una riga per ogni messaggio successivo finché la conferma resta
nella finestra di storico letta dall'`ai_check`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import LeadOutcome, OutcomeDefinition


@dataclass(slots=True, frozen=True)
class OutcomeCount:
    outcome_id: UUID
    key: str
    label: str
    total: int
    # Quanti dei conteggiati vengono da una sorgente certa (webhook / manuale)
    # invece che da un'inferenza dell'LLM. È ciò che permette alla bolla di dire
    # "312, di cui 180 verificati" invece di presentare un numero inferito come
    # se fosse un fatto.
    verified: int


_VERIFIED_SOURCES = ("webhook", "manual", "api")


class OutcomeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---- vocabolario ----------------------------------------------------

    async def list_definitions(
        self, *, tenant_id: UUID, merchant_id: UUID | None, enabled_only: bool = False
    ) -> list[OutcomeDefinition]:
        """Definizioni del merchant + libreria d'agenzia (`merchant_id` NULL)."""
        stmt = select(OutcomeDefinition).where(OutcomeDefinition.tenant_id == tenant_id)
        if merchant_id is not None:
            stmt = stmt.where(
                or_(
                    OutcomeDefinition.merchant_id == merchant_id,
                    OutcomeDefinition.merchant_id.is_(None),
                )
            )
        if enabled_only:
            stmt = stmt.where(OutcomeDefinition.enabled.is_(True))
        return list((await self._session.execute(stmt.order_by(OutcomeDefinition.label))).scalars())

    async def get_definition(self, outcome_id: UUID) -> OutcomeDefinition | None:
        stmt = select(OutcomeDefinition).where(OutcomeDefinition.id == outcome_id).limit(1)
        return (await self._session.execute(stmt)).scalars().first()

    async def get_definition_by_key(
        self, *, merchant_id: UUID, key: str
    ) -> OutcomeDefinition | None:
        stmt = (
            select(OutcomeDefinition)
            .where(OutcomeDefinition.merchant_id == merchant_id, OutcomeDefinition.key == key)
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def create_definition(
        self,
        *,
        tenant_id: UUID,
        merchant_id: UUID | None,
        key: str,
        label: str,
        description: str | None = None,
        source_kind: str = "ai_check",
        cardinality: str = "once_per_lead",
    ) -> OutcomeDefinition:
        definition = OutcomeDefinition(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            key=key,
            label=label,
            description=description,
            source_kind=source_kind,
            cardinality=cardinality,
        )
        self._session.add(definition)
        await self._session.flush()
        return definition

    async def update_definition(
        self,
        outcome_id: UUID,
        *,
        label: str | None = None,
        description: str | None = None,
        source_kind: str | None = None,
        enabled: bool | None = None,
    ) -> OutcomeDefinition | None:
        """`key` e `cardinality` non sono modificabili di proposito.

        La `key` è il riferimento stabile delle righe storiche. La `cardinality`
        è denormalizzata su ogni `lead_outcomes` e governa quale indice unique
        si applica: cambiarla a posteriori lascerebbe le righe vecchie sotto un
        vincolo e le nuove sotto un altro, cioè conteggi incoerenti. Se serve
        cambiarla, si crea una definizione nuova.
        """
        definition = await self.get_definition(outcome_id)
        if definition is None:
            return None
        if label is not None:
            definition.label = label
        if description is not None:
            definition.description = description
        if source_kind is not None:
            definition.source_kind = source_kind
        if enabled is not None:
            definition.enabled = enabled
        await self._session.flush()
        return definition

    async def delete_definition(self, outcome_id: UUID) -> bool:
        definition = await self.get_definition(outcome_id)
        if definition is None:
            return False
        # CASCADE porta via anche i fatti: cancellare una definizione è
        # cancellare la statistica, non nasconderla. Per smettere di misurare
        # senza perdere lo storico si usa `enabled=False`.
        await self._session.delete(definition)
        await self._session.flush()
        return True

    # ---- fatti -----------------------------------------------------------

    async def record(
        self,
        *,
        tenant_id: UUID,
        merchant_id: UUID,
        outcome_id: UUID,
        lead_id: UUID,
        cardinality: str,
        source: str,
        conversation_id: UUID | None = None,
        profile_id: UUID | None = None,
        automation_id: UUID | None = None,
        automation_node_key: str | None = None,
        confidence: float | None = None,
        value: dict[str, object] | None = None,
    ) -> bool:
        """Registra l'esito. True se la riga è nuova, False se era già presente.

        Il valore di ritorno è utile al chiamante per loggare "prima volta" vs
        "già registrato" senza fare una SELECT preventiva (che sarebbe comunque
        soggetta a race fra due run concorrenti dello stesso flusso).
        """
        stmt = (
            pg_insert(LeadOutcome)
            .values(
                tenant_id=tenant_id,
                merchant_id=merchant_id,
                outcome_id=outcome_id,
                lead_id=lead_id,
                conversation_id=conversation_id,
                profile_id=profile_id,
                automation_id=automation_id,
                automation_node_key=automation_node_key,
                source=source,
                confidence=confidence,
                value=value or {},
                cardinality=cardinality,
            )
            # Nessun `index_elements`: i vincoli sono indici *parziali* distinti
            # per cardinalità, e la forma nuda li copre tutti. Con
            # `cardinality='repeatable'` non c'è nessun indice unique e ogni
            # chiamata inserisce, che è esattamente l'intento.
            .on_conflict_do_nothing()
            .returning(LeadOutcome.id)
        )
        inserted = (await self._session.execute(stmt)).scalars().first()
        await self._session.flush()
        return inserted is not None

    async def has_outcome(
        self, *, lead_id: UUID, outcome_id: UUID, conversation_id: UUID | None = None
    ) -> bool:
        """Il lead ha già raggiunto questo esito?

        Serve alla condizione omonima della lavagnetta, che è il cancello più
        efficace prima di un `ai_check`: una conversazione che ha già l'esito
        esce definitivamente dal perimetro e non costa più nessuna chiamata LLM.
        """
        stmt = select(LeadOutcome.id).where(
            LeadOutcome.lead_id == lead_id, LeadOutcome.outcome_id == outcome_id
        )
        if conversation_id is not None:
            stmt = stmt.where(LeadOutcome.conversation_id == conversation_id)
        return (await self._session.execute(stmt.limit(1))).scalars().first() is not None

    # ---- conteggi (la terza bolla) ---------------------------------------

    async def count_by_outcome(
        self,
        *,
        merchant_id: UUID,
        since: datetime,
        outcome_ids: list[UUID] | None = None,
        profile_id: UUID | None = None,
        automation_id: UUID | None = None,
    ) -> dict[UUID, OutcomeCount]:
        """Conteggi per esito nella finestra, opzionalmente per profilo/automazione.

        Con `cardinality='once_per_lead'` — il default e il caso del questionario
        — un `COUNT(*)` è già il numero di **lead distinti**: lo garantisce
        l'indice unique, non un DISTINCT nella query.
        """
        verified = func.sum(case((LeadOutcome.source.in_(_VERIFIED_SOURCES), 1), else_=0))
        stmt = (
            select(
                LeadOutcome.outcome_id,
                OutcomeDefinition.key,
                OutcomeDefinition.label,
                func.count(LeadOutcome.id),
                verified,
            )
            .join(OutcomeDefinition, OutcomeDefinition.id == LeadOutcome.outcome_id)
            .where(LeadOutcome.merchant_id == merchant_id, LeadOutcome.occurred_at >= since)
            .group_by(LeadOutcome.outcome_id, OutcomeDefinition.key, OutcomeDefinition.label)
        )
        if outcome_ids is not None:
            if not outcome_ids:
                return {}
            stmt = stmt.where(LeadOutcome.outcome_id.in_(outcome_ids))
        if profile_id is not None:
            stmt = stmt.where(LeadOutcome.profile_id == profile_id)
        if automation_id is not None:
            stmt = stmt.where(LeadOutcome.automation_id == automation_id)

        rows = (await self._session.execute(stmt)).tuples().all()
        return {
            row[0]: OutcomeCount(
                outcome_id=row[0],
                key=str(row[1]),
                label=str(row[2]),
                total=int(row[3] or 0),
                verified=int(row[4] or 0),
            )
            for row in rows
        }
