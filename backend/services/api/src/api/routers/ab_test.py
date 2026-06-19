"""UC-09 — A/B experiment CRUD + metrics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ai_core.ab_stats import evaluate_significance
from api.dependencies.session import CurrentContext, DBSession
from config_resolver import ConfigKey, ConfigResolver
from db import ABRepository, PromptRepository
from db.session import TenantContext
from shared import NotFoundError, PermissionDeniedError

router = APIRouter()


class VariantIn(BaseModel):
    id: str
    weight: int = Field(..., ge=0, le=100)
    prompt_template_id: UUID | None = None
    # Authored system prompt for this arm. When set, it's persisted as a
    # versioned PromptTemplate (kind=system, variant_id=id) so the conversation
    # path resolves a *different* prompt per variant — the thing that makes the
    # A/B test actually compare two behaviours (UC-09).
    prompt_body: str | None = None


class ExperimentIn(BaseModel):
    name: str
    description: str | None = None
    variants: list[VariantIn]
    primary_metric: str
    # When omitted, falls back to the merchant's configured `ab_test.min_sample`
    # (the bot-config knob) so that per-merchant default is actually honoured.
    min_sample_size: int | None = None


class ExperimentOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    status: str
    variants: list[dict[str, Any]]
    primary_metric: str
    min_sample_size: int
    winner: str | None = None


class StopExperimentIn(BaseModel):
    # Optional explicit winner; when omitted the significance verdict from
    # `/metrics` is the source of truth and the experiment just ends.
    winner: str | None = None


@router.get("/", response_model=list[ExperimentOut])
async def list_experiments(ctx: CurrentContext, session: DBSession) -> list[ExperimentOut]:
    merchant_id = _require_merchant(ctx)
    repo = ABRepository(session)
    experiments = await repo.list_for_merchant(merchant_id)
    return [_to_out(e) for e in experiments]


@router.post("/", response_model=ExperimentOut)
async def create_experiment(
    payload: ExperimentIn, ctx: CurrentContext, session: DBSession
) -> ExperimentOut:
    merchant_id = _require_merchant(ctx)
    _validate_weights(payload.variants)

    repo = ABRepository(session)
    prompts = PromptRepository(session)

    variants_payload: list[dict[str, Any]] = []
    for v in payload.variants:
        entry: dict[str, Any] = {"id": v.id, "weight": v.weight}
        if v.prompt_template_id is not None:
            entry["prompt_template_id"] = str(v.prompt_template_id)
        if v.prompt_body and v.prompt_body.strip():
            template_id = await prompts.upsert_system_prompt(
                merchant_id=merchant_id,
                variant_id=v.id,
                body=v.prompt_body.strip(),
            )
            entry["prompt_template_id"] = str(template_id)
        variants_payload.append(entry)

    exp = await repo.create(
        merchant_id=merchant_id,
        name=payload.name,
        description=payload.description,
        variants=variants_payload,
        primary_metric=payload.primary_metric,
        min_sample_size=await _resolve_min_sample(session, merchant_id, payload.min_sample_size),
    )
    return _to_out(exp)


@router.post("/{experiment_id}/start", response_model=ExperimentOut)
async def start_experiment(
    experiment_id: UUID, ctx: CurrentContext, session: DBSession
) -> ExperimentOut:
    merchant_id = _require_merchant(ctx)
    repo = ABRepository(session)
    exp = await repo.get(experiment_id)
    if exp is None or exp.merchant_id != ctx.merchant_id:
        raise NotFoundError("Experiment not found")
    # One live experiment per merchant — otherwise variant assignment picks
    # ambiguously between concurrent experiments (UC-09).
    if exp.status != "running" and await repo.has_running(merchant_id, exclude_id=experiment_id):
        raise PermissionDeniedError(
            "Another experiment is already running for this merchant",
            error_code="experiment_already_running",
        )
    await repo.set_status(experiment_id, status="running", started_at=datetime.now(tz=UTC))
    exp = await repo.get(experiment_id)
    assert exp is not None
    return _to_out(exp)


@router.post("/{experiment_id}/stop", response_model=ExperimentOut)
async def stop_experiment(
    experiment_id: UUID, payload: StopExperimentIn, ctx: CurrentContext, session: DBSession
) -> ExperimentOut:
    _require_merchant(ctx)
    repo = ABRepository(session)
    exp = await repo.get(experiment_id)
    if exp is None or exp.merchant_id != ctx.merchant_id:
        raise NotFoundError("Experiment not found")
    await repo.stop(experiment_id, winner=payload.winner, ended_at=datetime.now(tz=UTC))
    exp = await repo.get(experiment_id)
    assert exp is not None
    return _to_out(exp)


@router.get("/{experiment_id}/metrics")
async def experiment_metrics(
    experiment_id: UUID, ctx: CurrentContext, session: DBSession
) -> dict[str, Any]:
    _require_merchant(ctx)
    repo = ABRepository(session)
    exp = await repo.get(experiment_id)
    if exp is None or exp.merchant_id != ctx.merchant_id:
        raise NotFoundError("Experiment not found")
    metrics = await repo.metrics(experiment_id)
    sig = evaluate_significance(
        [
            (m.variant_id, m.events_by_type.get(exp.primary_metric, 0), m.assignments)
            for m in metrics
        ]
    )
    total_assignments = sum(m.assignments for m in metrics)
    return {
        "experiment_id": str(experiment_id),
        "primary_metric": exp.primary_metric,
        "min_sample_size": exp.min_sample_size,
        "winner": exp.winner,
        "significance": {
            "winner": sig.winner,
            "p_value": sig.p_value,
            "significant": sig.significant,
            "confidence": sig.confidence,
            # Only trust the verdict once both arms have enough traffic.
            "enough_samples": total_assignments >= exp.min_sample_size,
        },
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


async def _resolve_min_sample(session: Any, merchant_id: UUID, explicit: int | None) -> int:
    """Explicit per-experiment value wins; otherwise the merchant's configured
    `ab_test.min_sample` default (UC-09), falling back to the system default."""
    if explicit is not None:
        return explicit
    value = await ConfigResolver(session).resolve(ConfigKey.AB_MIN_SAMPLE, merchant_id=merchant_id)
    return value if isinstance(value, int) else 100


def _require_merchant(ctx: TenantContext) -> UUID:
    """Assert merchant context and return the (now non-null) merchant_id."""
    if ctx.merchant_id is None:
        raise PermissionDeniedError(
            "Experiment endpoints require merchant context",
            error_code="no_merchant_context",
        )
    return ctx.merchant_id


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


def _to_out(exp: Any) -> ExperimentOut:
    return ExperimentOut(
        id=exp.id,
        name=exp.name,
        description=exp.description,
        status=exp.status,
        variants=exp.variants or [],
        primary_metric=exp.primary_metric,
        min_sample_size=exp.min_sample_size,
        winner=exp.winner,
    )
