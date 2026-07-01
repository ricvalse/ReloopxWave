"""Automazioni — the visual "lavagnetta" automation builder (graph CRUD).

A merchant draws trigger → condition → action graphs on a canvas. The canvas is
authoritative: every save replaces the whole node/edge set. The single trigger
node's event type is derived and denormalised onto the flow row so the worker
dispatcher can find subscribers cheaply.

A draft (`enabled=False`) can be saved even while incomplete; **enabling** a flow
requires a valid graph (one trigger, no cycles, required action config, …). All
routes are merchant-scoped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ai_core.automations import system_flow_timing_errors, validate_graph
from api.dependencies.session import CurrentContext, DBSession
from db import AutomationRepository
from db.models import AutomationFlow
from db.models.automation import ACTION_TYPES, CONDITION_TYPES, TRIGGER_TYPES
from shared import NotFoundError, PermissionDeniedError, get_logger

router = APIRouter()
logger = get_logger(__name__)


# ---- Models ----------------------------------------------------------------


class AutomationNodeIn(BaseModel):
    node_key: str = Field(max_length=64)
    kind: str = Field(max_length=16)  # trigger | condition | action
    type: str = Field(max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)
    position_x: float = 0.0
    position_y: float = 0.0


class AutomationEdgeIn(BaseModel):
    source_key: str = Field(max_length=64)
    target_key: str = Field(max_length=64)
    branch: str = Field(default="default", max_length=16)  # default | true | false


class AutomationUpsertIn(BaseModel):
    name: str = Field(max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool = False
    nodes: list[AutomationNodeIn] = Field(default_factory=list)
    edges: list[AutomationEdgeIn] = Field(default_factory=list)
    canvas: dict[str, Any] = Field(default_factory=dict)


class AutomationNodeOut(BaseModel):
    node_key: str
    kind: str
    type: str
    config: dict[str, Any]
    position_x: float
    position_y: float


class AutomationEdgeOut(BaseModel):
    source_key: str
    target_key: str
    branch: str


class AutomationOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    enabled: bool
    system_key: str | None
    is_system: bool
    trigger_type: str | None
    trigger_config: dict[str, Any]
    canvas: dict[str, Any]
    nodes: list[AutomationNodeOut]
    edges: list[AutomationEdgeOut]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, f: AutomationFlow) -> AutomationOut:
        nodes = sorted(f.nodes, key=lambda n: n.node_key)
        edges = sorted(f.edges, key=lambda e: (e.source_key, e.target_key, e.branch))
        return cls(
            id=f.id,
            name=f.name,
            description=f.description,
            enabled=f.enabled,
            system_key=f.system_key,
            is_system=f.system_key is not None,
            trigger_type=f.trigger_type,
            trigger_config=dict(f.trigger_config or {}),
            canvas=dict(f.canvas or {}),
            nodes=[
                AutomationNodeOut(
                    node_key=n.node_key,
                    kind=n.kind,
                    type=n.type,
                    config=dict(n.config or {}),
                    position_x=n.position_x,
                    position_y=n.position_y,
                )
                for n in nodes
            ],
            edges=[
                AutomationEdgeOut(source_key=e.source_key, target_key=e.target_key, branch=e.branch)
                for e in edges
            ],
            created_at=f.created_at,
            updated_at=f.updated_at,
        )


class AutomationCatalogOut(BaseModel):
    triggers: list[str]
    conditions: list[str]
    actions: list[str]


# ---- Routes ----------------------------------------------------------------


@router.get("/catalog", response_model=AutomationCatalogOut)
async def catalog(ctx: CurrentContext, session: DBSession) -> AutomationCatalogOut:
    """The node taxonomy that drives the editor palette (keeps FE/BE in sync)."""
    _require_merchant_scope(ctx)
    return AutomationCatalogOut(
        triggers=list(TRIGGER_TYPES),
        conditions=list(CONDITION_TYPES),
        actions=list(ACTION_TYPES),
    )


@router.get("", response_model=list[AutomationOut])
async def list_automations(ctx: CurrentContext, session: DBSession) -> list[AutomationOut]:
    merchant_id = _require_merchant_scope(ctx)
    repo = AutomationRepository(session)
    # The system lifecycle flows always appear on the canvas (seeded on demand).
    await repo.ensure_system_automations(merchant_id)
    rows = await repo.list_for_merchant(merchant_id)
    # `first_contact` is seeded but has no scheduler consumer yet (ADR 0011) —
    # hide it so we don't expose an editable-but-inert flow on the canvas.
    return [AutomationOut.from_model(r) for r in rows if r.system_key != "first_contact"]


@router.post("", response_model=AutomationOut, status_code=201)
async def create_automation(
    payload: AutomationUpsertIn, ctx: CurrentContext, session: DBSession
) -> AutomationOut:
    merchant_id = _require_merchant_scope(ctx)
    nodes, edges = _graph_dicts(payload)
    trigger_type, trigger_config = _validate_for_save(payload, nodes, edges)

    repo = AutomationRepository(session)
    flow = await repo.create(
        merchant_id=merchant_id,
        name=payload.name,
        description=payload.description,
        enabled=payload.enabled,
        trigger_type=trigger_type,
        trigger_config=trigger_config,
        canvas=payload.canvas,
    )
    await repo.replace_graph(flow, nodes=nodes, edges=edges)
    refreshed = await repo.get(flow.id)
    assert refreshed is not None
    logger.info(
        "automation.created",
        actor_id=str(ctx.actor_id),
        merchant_id=str(merchant_id),
        automation_id=str(flow.id),
        enabled=payload.enabled,
    )
    return AutomationOut.from_model(refreshed)


@router.get("/{automation_id}", response_model=AutomationOut)
async def get_automation(
    automation_id: UUID, ctx: CurrentContext, session: DBSession
) -> AutomationOut:
    merchant_id = _require_merchant_scope(ctx)
    repo = AutomationRepository(session)
    await repo.ensure_system_automations(merchant_id)
    flow = await repo.get(automation_id)
    if flow is None:
        raise NotFoundError("Automation not found", automation_id=str(automation_id))
    return AutomationOut.from_model(flow)


@router.put("/{automation_id}", response_model=AutomationOut)
async def update_automation(
    automation_id: UUID,
    payload: AutomationUpsertIn,
    ctx: CurrentContext,
    session: DBSession,
) -> AutomationOut:
    merchant_id = _require_merchant_scope(ctx)
    repo = AutomationRepository(session)
    flow = await repo.get(automation_id)
    if flow is None:
        raise NotFoundError("Automation not found", automation_id=str(automation_id))

    if flow.system_key is not None:
        _guard_system_flow_edit(flow, payload)

    nodes, edges = _graph_dicts(payload)
    trigger_type, trigger_config = _validate_for_save(payload, nodes, edges)

    await repo.update_meta(
        flow,
        name=payload.name,
        description=payload.description,
        enabled=payload.enabled,
        trigger_type=trigger_type,
        trigger_config=trigger_config,
        canvas=payload.canvas,
    )
    await repo.replace_graph(flow, nodes=nodes, edges=edges)
    refreshed = await repo.get(flow.id)
    assert refreshed is not None
    logger.info(
        "automation.updated",
        actor_id=str(ctx.actor_id),
        merchant_id=str(merchant_id),
        automation_id=str(automation_id),
        enabled=payload.enabled,
    )
    return AutomationOut.from_model(refreshed)


@router.delete("/{automation_id}", status_code=204)
async def delete_automation(automation_id: UUID, ctx: CurrentContext, session: DBSession) -> None:
    _require_merchant_scope(ctx)
    repo = AutomationRepository(session)
    flow = await repo.get(automation_id)
    if flow is None:
        raise NotFoundError("Automation not found", automation_id=str(automation_id))
    if flow.system_key is not None:
        raise HTTPException(status_code=422, detail="system automations cannot be deleted")
    await repo.delete(flow)


# ---- helpers ---------------------------------------------------------------


def _require_merchant_scope(ctx: CurrentContext) -> UUID:
    if ctx.merchant_id is None:
        raise PermissionDeniedError(
            "Merchant context required for automation management",
            error_code="no_merchant_context",
        )
    return ctx.merchant_id


def _graph_dicts(
    payload: AutomationUpsertIn,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = [n.model_dump() for n in payload.nodes]
    edges = [e.model_dump() for e in payload.edges]
    return nodes, edges


def _validate_for_save(
    payload: AutomationUpsertIn,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any]]:
    """Derive the trigger; hard-block only when *enabling* an invalid graph.

    A disabled draft can be saved incomplete (work-in-progress on the canvas);
    a flow can only be enabled once its graph is valid — the engine runs enabled
    flows only, so this is the safety boundary.
    """
    result = validate_graph(nodes, edges)
    if payload.enabled and not result.ok:
        raise HTTPException(
            status_code=422,
            detail={"errors": result.errors, "message": "fix the flow before enabling it"},
        )
    return result.trigger_type, result.trigger_config


# Action types a system flow may contain. System flows are resolved SYNCHRONOUSLY
# by the schedulers (`resolve_send_plan`/`resolve_send_node_at`), so IO-bound nodes
# (ai_reply, set_lead_field, human_handoff) and the async `ai_check` condition are
# forbidden — placed here they would fail-closed silently. `send`/`wait`/
# `wait_until_before` carry the content + timing the schedulers read (ADR 0011).
_SYSTEM_FLOW_ACTIONS = frozenset({"send", "wait", "wait_until_before"})


def _guard_system_flow_edit(flow: AutomationFlow, payload: AutomationUpsertIn) -> None:
    """System lifecycle flows: the trigger is locked; only send / wait /
    wait_until_before actions and synchronous conditions are allowed; and the
    timing must stay within the compliance ranges when the flow is enabled."""
    triggers = [n for n in payload.nodes if n.kind == "trigger"]
    if len(triggers) != 1 or triggers[0].type != flow.trigger_type:
        raise HTTPException(
            status_code=422, detail="the trigger of a system flow cannot be changed"
        )
    for n in payload.nodes:
        if n.kind == "action" and n.type not in _SYSTEM_FLOW_ACTIONS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"system flows do not allow the '{n.type}' action "
                    "(use 'send', 'wait' or 'wait_until_before')"
                ),
            )
        if n.kind == "condition" and n.type == "ai_check":
            raise HTTPException(
                status_code=422,
                detail="system flows cannot use the AI condition (resolved synchronously)",
            )
    # Compliance/anti-spam timing ranges: enforced only when ENABLING (a disabled
    # draft may be saved incomplete, mirroring `_validate_for_save`).
    if payload.enabled and flow.system_key is not None:
        nodes = [nn.model_dump() for nn in payload.nodes]
        edges = [ee.model_dump() for ee in payload.edges]
        errors = system_flow_timing_errors(flow.system_key, nodes, edges)
        if errors:
            raise HTTPException(
                status_code=422,
                detail={
                    "errors": errors,
                    "message": "correggi i tempi del flusso prima di attivarlo",
                },
            )
