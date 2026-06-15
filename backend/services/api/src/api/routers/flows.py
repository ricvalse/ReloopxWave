"""Flussi — configurable outbound sequences (scope A).

A flow per lifecycle `key` (no_answer | reactivation | booking_reminder |
first_contact) holds an ordered list of steps; each step binds a delay + an
(optional) approved template + variable mapping + 24h-window policy. The
schedulers read these steps via `FlowRepository.resolve_step`. Merchant-scoped.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.dependencies.session import CurrentContext, DBSession
from db import LIFECYCLE_FLOW_KEYS, FlowRepository
from db.models import Flow
from shared import PermissionDeniedError, get_logger

router = APIRouter()
logger = get_logger(__name__)

_VALID_WINDOW_POLICIES = ("auto", "require_template", "freeform_only")


# ---- Models ----------------------------------------------------------------


class FlowStepIn(BaseModel):
    step_index: int = Field(ge=0)
    delay_minutes: int = Field(default=0, ge=0)
    template_id: UUID | None = None
    variable_mapping: dict[str, str] = Field(default_factory=dict)
    window_policy: str = Field(default="auto")
    free_text: str | None = Field(default=None, max_length=1000)
    enabled: bool = True


class FlowUpsertIn(BaseModel):
    name: str = Field(max_length=200)
    enabled: bool = True
    steps: list[FlowStepIn] = Field(default_factory=list)


class FlowStepOut(BaseModel):
    id: UUID
    step_index: int
    delay_minutes: int
    template_id: UUID | None
    variable_mapping: dict[str, str]
    window_policy: str
    free_text: str | None
    enabled: bool


class FlowOut(BaseModel):
    id: UUID
    key: str
    name: str
    enabled: bool
    steps: list[FlowStepOut]

    @classmethod
    def from_model(cls, flow: Flow) -> FlowOut:
        return cls(
            id=flow.id,
            key=flow.key,
            name=flow.name,
            enabled=flow.enabled,
            steps=[
                FlowStepOut(
                    id=s.id,
                    step_index=s.step_index,
                    delay_minutes=s.delay_minutes,
                    template_id=s.template_id,
                    variable_mapping=dict(s.variable_mapping or {}),
                    window_policy=s.window_policy,
                    free_text=s.free_text,
                    enabled=s.enabled,
                )
                for s in sorted(flow.steps, key=lambda x: x.step_index)
            ],
        )


# ---- Routes ----------------------------------------------------------------


@router.get("", response_model=list[FlowOut])
async def list_flows(ctx: CurrentContext, session: DBSession) -> list[FlowOut]:
    merchant_id = _require_merchant_scope(ctx)
    flows = await FlowRepository(session).list_for_merchant(merchant_id)
    return [FlowOut.from_model(f) for f in flows]


@router.put("/{key}", response_model=FlowOut)
async def upsert_flow(
    key: str, payload: FlowUpsertIn, ctx: CurrentContext, session: DBSession
) -> FlowOut:
    merchant_id = _require_merchant_scope(ctx)
    if key not in LIFECYCLE_FLOW_KEYS:
        raise HTTPException(status_code=422, detail=f"key must be one of {LIFECYCLE_FLOW_KEYS}")
    for step in payload.steps:
        if step.window_policy not in _VALID_WINDOW_POLICIES:
            raise HTTPException(
                status_code=422,
                detail=f"window_policy must be one of {_VALID_WINDOW_POLICIES}",
            )

    repo = FlowRepository(session)
    flow = await repo.upsert_flow(
        merchant_id=merchant_id, key=key, name=payload.name, enabled=payload.enabled
    )
    # Re-index by array position so the server is authoritative: the client's
    # step_index is advisory only. This guarantees a unique, contiguous 0..n-1
    # sequence and avoids an IntegrityError (uq_flow_steps_flow_id) on duplicates.
    step_specs: list[dict[str, Any]] = [
        {
            "step_index": i,
            "delay_minutes": s.delay_minutes,
            "template_id": s.template_id,
            "variable_mapping": s.variable_mapping,
            "window_policy": s.window_policy,
            "free_text": s.free_text,
            "enabled": s.enabled,
        }
        for i, s in enumerate(payload.steps)
    ]
    await repo.replace_steps(flow, steps=step_specs)
    # Re-read with steps eagerly loaded for the response.
    refreshed = await repo.get_by_key(merchant_id, key)
    assert refreshed is not None
    logger.info(
        "flow.upserted",
        actor_id=str(ctx.actor_id),
        merchant_id=str(merchant_id),
        key=key,
        steps=len(payload.steps),
    )
    return FlowOut.from_model(refreshed)


# ---- helpers ---------------------------------------------------------------


def _require_merchant_scope(ctx: CurrentContext) -> UUID:
    if ctx.merchant_id is None:
        raise PermissionDeniedError(
            "Merchant context required for flow management",
            error_code="no_merchant_context",
        )
    return ctx.merchant_id
