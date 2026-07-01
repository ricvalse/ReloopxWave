"""Automation (graph flow) persistence — CRUD + trigger dispatch lookup.

The router replaces the whole node/edge set on each save (the canvas is the
source of truth), so there's no per-node patch API. The worker dispatcher calls
`list_enabled_by_trigger` to find the automations that subscribe to an event.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import AutomationEdge, AutomationFlow, AutomationNode


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
