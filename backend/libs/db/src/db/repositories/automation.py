"""Automation (graph flow) persistence — CRUD + trigger dispatch lookup.

The router replaces the whole node/edge set on each save (the canvas is the
source of truth), so there's no per-node patch API. The worker dispatcher calls
`list_enabled_by_trigger` to find the automations that subscribe to an event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    AutomationEdge,
    AutomationFlow,
    AutomationHoursQueue,
    AutomationNode,
)


class AutomationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _with_graph(self, stmt: Any) -> Any:
        return stmt.options(
            selectinload(AutomationFlow.nodes),
            selectinload(AutomationFlow.edges),
        )

    async def list_for_merchant(self, merchant_id: UUID) -> list[AutomationFlow]:
        stmt = self._with_graph(
            select(AutomationFlow)
            .where(AutomationFlow.merchant_id == merchant_id)
            .order_by(AutomationFlow.created_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars())

    async def get(self, automation_id: UUID) -> AutomationFlow | None:
        stmt = self._with_graph(select(AutomationFlow).where(AutomationFlow.id == automation_id))
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_enabled_by_trigger(
        self, *, merchant_id: UUID, trigger_type: str
    ) -> list[AutomationFlow]:
        """Enabled automations for a merchant that fire on `trigger_type`.

        Drives the worker dispatcher — called once per matching analytics event.
        """
        stmt = self._with_graph(
            select(AutomationFlow).where(
                AutomationFlow.merchant_id == merchant_id,
                AutomationFlow.trigger_type == trigger_type,
                AutomationFlow.enabled.is_(True),
            )
        )
        return list((await self._session.execute(stmt)).scalars())

    async def enabled_trigger_thresholds(
        self, *, trigger_type: str, config_key: str, default: int
    ) -> dict[UUID, list[int]]:
        """Le soglie dichiarate sui nodi trigger abilitati, per merchant.

        Scansione cross-tenant: la usano gli sweep, che girano fuori da una
        sessione tenant. Ogni trigger tiene la propria soglia sotto una chiave
        diversa e nella propria unità — `delay_minutes` per "nessuna risposta",
        `days` per "lead dormiente" — quindi la chiave è un parametro e i valori
        tornano grezzi, nell'unità in cui il merchant li ha scritti.

        Il senso è sempre lo stesso: **il pavimento della scansione si deriva da
        ciò che i merchant hanno davvero configurato**, non da una costante. Una
        costante qui dentro è una soglia fantasma — la UI accetta il valore, il
        backend lo ignora, e nessuno dei due lo dice.

        Un nodo trigger senza la chiave (o con un valore non positivo) vale
        `default`.
        """
        stmt = (
            select(
                AutomationFlow.merchant_id,
                AutomationNode.config[config_key].astext,
            )
            .join(AutomationNode, AutomationNode.automation_id == AutomationFlow.id)
            .where(
                AutomationFlow.trigger_type == trigger_type,
                AutomationFlow.enabled.is_(True),
                AutomationNode.kind == "trigger",
            )
        )
        out: dict[UUID, list[int]] = {}
        for merchant_id, raw in (await self._session.execute(stmt)).all():
            out.setdefault(merchant_id, []).append(self._normalizza_soglia(raw, default=default))
        return out

    @staticmethod
    def _normalizza_soglia(raw: str | None, *, default: int) -> int:
        """Il valore grezzo del nodo trigger, letto come intero positivo.

        Estratto perché è l'unico punto in cui un valore scritto storto sul grafo
        (campo svuotato, zero, testo) diventa silenziosamente il default: merita
        di essere verificabile da solo, senza database.
        """
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    async def enabled_trigger_delays(
        self, *, trigger_type: str, default_minutes: int
    ) -> dict[UUID, list[int]]:
        """Ritardi (`delay_minutes`) dei nodi trigger abilitati, per merchant.

        Serve a due cose che devono restare d'accordo fra loro:

          * l'emettitore no-answer prende il **minimo** globale come pavimento
            della scansione, così un ritardo di 10 minuti funziona davvero e una
            piattaforma senza automazioni no_answer non scansiona affatto;
          * lo sweep di chiusura prende il **massimo per merchant** come soglia
            minima di inattività, così non chiude una conversazione che ha
            ancora un follow-up in arrivo (era il bug: chiusura e follow-up
            avevano entrambi 120 minuti e la chiusura vinceva).
        """
        return await self.enabled_trigger_thresholds(
            trigger_type=trigger_type, config_key="delay_minutes", default=default_minutes
        )

    async def create(
        self,
        *,
        merchant_id: UUID,
        name: str,
        description: str | None = None,
        enabled: bool = False,
        trigger_type: str | None = None,
        trigger_config: dict[str, Any] | None = None,
        canvas: dict[str, Any] | None = None,
    ) -> AutomationFlow:
        flow = AutomationFlow(
            merchant_id=merchant_id,
            name=name,
            description=description,
            enabled=enabled,
            trigger_type=trigger_type,
            trigger_config=trigger_config or {},
            canvas=canvas or {},
        )
        self._session.add(flow)
        await self._session.flush()
        return flow

    async def update_meta(
        self,
        flow: AutomationFlow,
        *,
        name: str,
        description: str | None,
        enabled: bool,
        trigger_type: str | None,
        trigger_config: dict[str, Any] | None,
        canvas: dict[str, Any] | None,
    ) -> AutomationFlow:
        flow.name = name
        flow.description = description
        flow.enabled = enabled
        flow.trigger_type = trigger_type
        flow.trigger_config = trigger_config or {}
        flow.canvas = canvas or {}
        await self._session.flush()
        return flow

    async def replace_graph(
        self, flow: AutomationFlow, *, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
    ) -> None:
        """Swap the entire node/edge set. The canvas is authoritative on save."""
        # Load the collections explicitly: on a freshly-created flow they have
        # never been loaded, and the implicit sync lazy-load would raise
        # MissingGreenlet under AsyncSession (the update path arrives here
        # already eager-loaded via get(); the refresh is a no-op query there).
        await self._session.refresh(flow, attribute_names=["nodes", "edges"])
        for node in list(flow.nodes):
            await self._session.delete(node)
        for edge in list(flow.edges):
            await self._session.delete(edge)
        await self._session.flush()

        for spec in nodes:
            self._session.add(
                AutomationNode(
                    automation_id=flow.id,
                    merchant_id=flow.merchant_id,
                    node_key=str(spec["node_key"]),
                    kind=str(spec["kind"]),
                    type=str(spec["type"]),
                    config=spec.get("config") or {},
                    position_x=float(spec.get("position_x", 0.0)),
                    position_y=float(spec.get("position_y", 0.0)),
                )
            )
        for spec in edges:
            self._session.add(
                AutomationEdge(
                    automation_id=flow.id,
                    merchant_id=flow.merchant_id,
                    source_key=str(spec["source_key"]),
                    target_key=str(spec["target_key"]),
                    branch=str(spec.get("branch", "default")),
                )
            )
        await self._session.flush()

    async def delete(self, flow: AutomationFlow) -> None:
        await self._session.delete(flow)
        await self._session.flush()


@dataclass(slots=True, frozen=True)
class QueuedAutomationRun:
    """Una riga di `automation_hours_queue` pronta da riprendere (ADR 0030)."""

    id: UUID
    tenant_id: UUID
    merchant_id: UUID
    automation_id: UUID
    subject_type: str
    subject_id: UUID
    node_keys: list[str]
    episode_anchor: str | None
    dedup_key: str
    queued_at: datetime


class AutomationHoursQueueRepository:
    """La coda dei rami di automazione sospesi fuori orario (ADR 0030).

    Stessa forma di `ConversationRepository.list_off_hours_pending` +
    `claim_off_hours_resume`: scansione cross-tenant **senza** filtro orario —
    gli orari dipendono dalla cascata di config per merchant e li risolve lo
    sweep in Python, su valori freschi a ogni passata. È il motivo per cui
    questo è uno sweep e non un job differito: il momento dell'apertura cambia.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        *,
        tenant_id: UUID,
        merchant_id: UUID,
        automation_id: UUID,
        subject_type: str,
        subject_id: UUID,
        node_keys: list[str],
        episode_anchor: str | None,
        dedup_key: str,
    ) -> bool:
        """Accoda un ramo sospeso. Idempotente su `dedup_key`.

        `ON CONFLICT DO NOTHING`: la ri-scansione del dispatcher entro i 120s di
        lookback, o una ri-consegna arq, ripassano dagli stessi nodi con la
        stessa chiave — accodare due volte significherebbe due messaggi identici
        alla riapertura. Ritorna True solo per l'inserimento vincente, così il
        chiamante può emettere l'evento una volta sola.
        """
        stmt = (
            pg_insert(AutomationHoursQueue)
            .values(
                tenant_id=tenant_id,
                merchant_id=merchant_id,
                automation_id=automation_id,
                subject_type=subject_type,
                subject_id=subject_id,
                node_keys=list(node_keys),
                episode_anchor=episode_anchor,
                dedup_key=dedup_key,
            )
            .on_conflict_do_nothing(constraint="uq_automation_hours_queue_dedup")
            .returning(AutomationHoursQueue.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def list_pending(self, *, limit: int = 500) -> list[QueuedAutomationRun]:
        """Scansione cross-tenant, i più vecchi per primi.

        Il cap fa sì che il pool si dreni da solo; con l'ordinamento per attesa,
        se tronca a restare indietro è chi ha aspettato meno.
        """
        stmt = select(AutomationHoursQueue).order_by(AutomationHoursQueue.queued_at).limit(limit)
        rows = (await self._session.execute(stmt)).scalars()
        return [
            QueuedAutomationRun(
                id=r.id,
                tenant_id=r.tenant_id,
                merchant_id=r.merchant_id,
                automation_id=r.automation_id,
                subject_type=r.subject_type,
                subject_id=r.subject_id,
                node_keys=[str(k) for k in (r.node_keys or [])],
                episode_anchor=r.episode_anchor,
                dedup_key=r.dedup_key,
                queued_at=r.queued_at,
            )
            for r in rows
        ]

    async def claim(self, queue_id: UUID, *, stale_after_minutes: int = 15) -> bool:
        """Prende in carico una riga. Compare-and-swap: vince una sola passata.

        Due passate dello sweep possono sovrapporsi e senza claim il cliente
        riceverebbe due volte lo stesso messaggio alla riapertura. Il claim
        scade da solo dopo `stale_after_minutes`, altrimenti un worker morto a
        metà bloccherebbe la riga per sempre.

        Il cast è scritto `CAST(:nome AS uuid)` e non con la sintassi a due
        due-punti: quella lascia il parametro letterale invece di legarlo, e
        l'UPDATE muore con un syntax error (guardia in
        tests/unit/test_sql_bind_params.py, che questo modulo ha già fatto
        scattare una volta — dal commento, non dal codice).
        """
        result = await self._session.execute(
            text(
                """
                UPDATE automation_hours_queue
                SET claimed_at = now(), updated_at = now()
                WHERE id = CAST(:queue_id AS uuid)
                  AND (
                      claimed_at IS NULL
                      OR claimed_at < now() - make_interval(mins => :stale_after)
                  )
                RETURNING id
                """
            ),
            {"queue_id": str(queue_id), "stale_after": stale_after_minutes},
        )
        return result.first() is not None

    async def release(self, queue_id: UUID) -> None:
        """Libera il claim lasciando la riga in coda: il prossimo tick riprova.

        Un riaccodamento fallito non deve consumare l'attesa in silenzio.
        """
        await self._session.execute(
            text(
                """
                UPDATE automation_hours_queue
                SET claimed_at = NULL, updated_at = now()
                WHERE id = CAST(:queue_id AS uuid)
                """
            ),
            {"queue_id": str(queue_id)},
        )

    async def delete(self, queue_id: UUID) -> None:
        """Toglie la riga: ripresa avvenuta, oppure caduta (scaduta/orfana)."""
        await self._session.execute(
            delete(AutomationHoursQueue).where(AutomationHoursQueue.id == queue_id)
        )
