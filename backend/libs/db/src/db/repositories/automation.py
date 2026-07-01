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
from db.models.automation import SYSTEM_AUTOMATION_KEYS

# Per-system-flow trigger type (visual anchor — system flows are scheduler-driven,
# never event-dispatched, so the trigger node is just the locked entry point) and
# the default display name used when lazily seeding a missing system flow.
SYSTEM_TRIGGER_TYPE: dict[str, str] = {
    "no_answer": "no_answer",
    "reactivation": "lead_dormant",
    "booking_reminder": "booking_created",
    "first_contact": "message_received",
}
SYSTEM_FLOW_NAMES: dict[str, str] = {
    "no_answer": "Nessuna risposta",
    "reactivation": "Riattivazione dormienti",
    "booking_reminder": "Promemoria appuntamento",
    "first_contact": "Primo contatto",
}

# Default send node config — free_text=None so the scheduler's built-in per-attempt
# copy (or the merchant's config text) is used until the merchant edits it.
_DEFAULT_SEND: dict[str, Any] = {
    "window_policy": "auto",
    "free_text": None,
    "template_id": None,
    "variable_mapping": {},
}

# Default graph (linear chain trigger → …) seeded per system_key. Mirrors the
# config defaults (NoAnswerConfig 120/1440/max 2, ReactivationConfig 90/7/max 3,
# booking.reminder_schedule [24]) so ENABLING a freshly-seeded flow preserves the
# out-of-the-box behaviour instead of silently collapsing to a single send (ADR 0011).
_DEFAULT_SYSTEM_GRAPH: dict[str, list[tuple[str, str, dict[str, Any]]]] = {
    "no_answer": [
        ("trigger", "no_answer", {"delay_minutes": 120}),
        ("action", "send", dict(_DEFAULT_SEND)),
        ("action", "wait", {"minutes": 1440, "unit": "minutes"}),
        ("action", "send", dict(_DEFAULT_SEND)),
    ],
    "reactivation": [
        ("trigger", "lead_dormant", {"days": 90}),
        ("action", "send", dict(_DEFAULT_SEND)),
        ("action", "wait", {"minutes": 7, "unit": "days"}),
        ("action", "send", dict(_DEFAULT_SEND)),
        ("action", "wait", {"minutes": 7, "unit": "days"}),
        ("action", "send", dict(_DEFAULT_SEND)),
    ],
    "booking_reminder": [
        ("trigger", "booking_created", {}),
        ("action", "wait_until_before", {"anchor": "appointment.start_at", "hours": 24}),
        ("action", "send", dict(_DEFAULT_SEND)),
    ],
    "first_contact": [
        ("trigger", "message_received", {}),
        ("action", "send", dict(_DEFAULT_SEND)),
    ],
}


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
        System flows (`system_key` set) are EXCLUDED: they are scheduler-driven,
        so dispatching them off events would double-execute them.
        """
        stmt = self._with_graph(
            select(AutomationFlow).where(
                AutomationFlow.merchant_id == merchant_id,
                AutomationFlow.trigger_type == trigger_type,
                AutomationFlow.enabled.is_(True),
                AutomationFlow.system_key.is_(None),
            )
        )
        return list((await self._session.execute(stmt)).scalars())

    async def get_by_system_key(self, merchant_id: UUID, system_key: str) -> AutomationFlow | None:
        """The merchant's system lifecycle flow for `system_key` (with its graph)."""
        stmt = self._with_graph(
            select(AutomationFlow).where(
                AutomationFlow.merchant_id == merchant_id,
                AutomationFlow.system_key == system_key,
            )
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def ensure_system_automations(self, merchant_id: UUID) -> None:
        """Lazily seed the system lifecycle flows so they always appear on the
        canvas. Idempotent: each missing `system_key` gets its default graph
        (`_DEFAULT_SYSTEM_GRAPH`, a linear trigger → send/wait chain mirroring the
        config defaults), disabled by default."""
        existing = set(
            (
                await self._session.execute(
                    select(AutomationFlow.system_key).where(
                        AutomationFlow.merchant_id == merchant_id,
                        AutomationFlow.system_key.is_not(None),
                    )
                )
            ).scalars()
        )
        for key in SYSTEM_AUTOMATION_KEYS:
            if key in existing:
                continue
            spec = _DEFAULT_SYSTEM_GRAPH[key]
            trigger_cfg = next((cfg for kind, _t, cfg in spec if kind == "trigger"), {})
            # ADR 0011: system flows are ON by default (their default graph mirrors
            # the config defaults, so this matches the historical "no flow → send
            # with defaults" behaviour). The merchant toggles a flow off on the canvas.
            # first_contact stays off — it has no scheduler consumer yet (hidden in UI).
            flow = AutomationFlow(
                merchant_id=merchant_id,
                name=SYSTEM_FLOW_NAMES[key],
                enabled=key != "first_contact",
                system_key=key,
                trigger_type=SYSTEM_TRIGGER_TYPE[key],
                trigger_config=dict(trigger_cfg),
                canvas={},
            )
            self._session.add(flow)
            await self._session.flush()

            prev_key: str | None = None
            for i, (kind, ntype, config) in enumerate(spec):
                node_key = "t" if kind == "trigger" else f"n{i}"
                self._session.add(
                    AutomationNode(
                        automation_id=flow.id,
                        merchant_id=merchant_id,
                        node_key=node_key,
                        kind=kind,
                        type=ntype,
                        config=dict(config),
                        position_x=160.0,
                        position_y=60.0 + i * 90.0,
                    )
                )
                if prev_key is not None:
                    self._session.add(
                        AutomationEdge(
                            automation_id=flow.id,
                            merchant_id=merchant_id,
                            source_key=prev_key,
                            target_key=node_key,
                            branch="default",
                        )
                    )
                prev_key = node_key
        await self._session.flush()

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
