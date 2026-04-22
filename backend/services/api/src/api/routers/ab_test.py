"""UC-09 — A/B experiment CRUD + metrics."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.dependencies.session import CurrentContext, DBSession
from db import ABRepository
from shared import NotFoundError, PermissionDeniedError

router = APIRouter()


class VariantIn(BaseModel):
    id: str
    weight: int = Field(..., ge=0, le=100)
    prompt_template_id: UUID | None = None


class ExperimentIn(BaseModel):
    name: str
    description: str | None = None
    variants: list[VariantIn]
    primary_metric: str
    min_sample_size: int = 100


class ExperimentOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    status: str
    variants: list[dict[str, Any]]
    primary_metric: str
    min_sample_size: int


@router.get("/", response_model=list[ExperimentOut])
async def list_experiments(ctx: CurrentContext, session: DBSession) -> list[ExperimentOut]:
    _require_merchant(ctx)
    repo = ABRepository(session)
    experiments = await repo.list_active_for_merchant(ctx.merchant_id)  # type: ignore[arg-type]
    return [_to_out(e) for e in experiments]


@router.post("/", response_model=ExperimentOut)
async def create_experiment(
    payload: ExperimentIn, ctx: CurrentContext, session: DBSession
) -> ExperimentOut:
    _require_merchant(ctx)
    _validate_weights(payload.variants)

    repo = ABRepository(session)
    exp = await repo.create(
        merchant_id=ctx.merchant_id,  # type: ignore[arg-type]
        name=payload.name,
        description=payload.description,
        variants=[v.model_dump() for v in payload.variants],
        primary_metric=payload.primary_metric,
        min_sample_size=payload.min_sample_size,
    )
    return _to_out(exp)


@router.post("/{experiment_id}/start", response_model=ExperimentOut)
async def start_experiment(
    experiment_id: UUID, ctx: CurrentContext, session: DBSession
) -> ExperimentOut:
    _require_merchant(ctx)
    repo = ABRepository(session)
    exp = await repo.get(experiment_id)
    if exp is None or exp.merchant_id != ctx.merchant_id:
        raise NotFoundError("Experiment not found")
    await repo.set_status(
        experiment_id, status="running", started_at=datetime.now(tz=timezone.utc)
    )
    exp = await repo.get(experiment_id)
    assert exp is not None
    return _to_out(exp)


@router.get("/{experiment_id}/metrics")
async def experiment_metrics(
    experiment_id: UUID, ctx: CurrentContext, session: DBSession
) -> dict:
    _require_merchant(ctx)
    repo = ABRepository(session)
    exp = await repo.get(experiment_id)
    if exp is None or exp.merchant_id != ctx.merchant_id:
        raise NotFoundError("Experiment not found")
    metrics = await repo.metrics(experiment_id)
    return {
        "experiment_id": str(experiment_id),
        "primary_metric": exp.primary_metric,
        "min_sample_size": exp.min_sample_size,
        "variants": [
            {
                "variant_id": m.variant_id,
                "assignments": m.assignments,
                "events": m.events_by_type,
                "primary_metric_count": m.events_by_type.get(exp.primary_metric, 0),
                "rate": (
                    m.events_by_type.get(exp.primary_metric, 0) / m.assignments
                    if m.assignments
                    else 0.0
                ),
            }
            for m in metrics
        ],
    }


def _require_merchant(ctx) -> None:
    if ctx.merchant_id is None:
        raise PermissionDeniedError(
            "Experiment endpoints require merchant context",
            error_code="no_merchant_context",
        )


def _validate_weights(variants: list[VariantIn]) -> None:
    ids = [v.id for v in variants]
    if len(ids) != len(set(ids)):
        raise PermissionDeniedError("Variant ids must be unique", error_code="dup_variant_ids")
    total = sum(v.weight for v in variants)
    if total != 100:
        raise PermissionDeniedError(
            f"Variant weights must sum to 100 (got {total})",
            error_code="weights_not_100",
        )


def _to_out(exp) -> ExperimentOut:
    return ExperimentOut(
        id=exp.id,
        name=exp.name,
        description=exp.description,
        status=exp.status,
        variants=exp.variants or [],
        primary_metric=exp.primary_metric,
        min_sample_size=exp.min_sample_size,
    )
