"""UC-13 — objection report + trigger endpoint."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from api.dependencies.auth import require_role
from api.dependencies.session import CurrentContext, DBSession
from db import ObjectionRepository
from shared import PermissionDeniedError

router = APIRouter()


@router.get("/objections")
async def objection_report(
    ctx: CurrentContext,
    session: DBSession,
    since_days: int = Query(30, ge=1, le=365),
    samples_per_category: int = Query(3, ge=0, le=10),
    variant_id: str | None = Query(default=None, description="Filter to one A/B variant (UC-13)"),
) -> dict[str, Any]:
    if ctx.merchant_id is None:
        raise PermissionDeniedError(
            "Objection report requires a merchant context",
            error_code="no_merchant_context",
        )
    repo = ObjectionRepository(session)
    categories = await repo.category_histogram(
        merchant_id=ctx.merchant_id, since_days=since_days, bot_variant=variant_id
    )

    payload = []
    for cat in categories:
        samples = (
            await repo.recent_samples(
                merchant_id=ctx.merchant_id,
                category=cat.category,
                limit=samples_per_category,
                bot_variant=variant_id,
            )
            if samples_per_category > 0
            else []
        )
        payload.append(
            {
                "category": cat.category,
                "count": cat.count,
                "samples": samples,
            }
        )

    # Per-day, per-category series powering the heatmap + trend view (UC-13).
    trend = await repo.category_histogram_by_day(
        merchant_id=ctx.merchant_id, since_days=since_days, bot_variant=variant_id
    )
    return {
        "since_days": since_days,
        "variant_id": variant_id,
        "categories": payload,
        "trend": trend,
    }


@router.get(
    "/objections/agency",
    dependencies=[Depends(require_role("agency_admin"))],
)
async def objection_report_agency(
    ctx: CurrentContext,
    session: DBSession,
    since_days: int = Query(30, ge=1, le=365),
    variant_id: str | None = Query(default=None, description="Filter to one A/B variant (UC-13)"),
) -> dict[str, Any]:
    """Tenant-wide objection histogram across every merchant of the agency (UC-13)."""
    repo = ObjectionRepository(session)
    categories = await repo.category_histogram_tenant(
        tenant_id=ctx.tenant_id, since_days=since_days, bot_variant=variant_id
    )
    payload = [{"category": c.category, "count": c.count} for c in categories]
    return {"since_days": since_days, "variant_id": variant_id, "categories": payload}


@router.post("/objections/extract/{conversation_id}")
async def trigger_extraction(
    conversation_id: UUID, request: Request, ctx: CurrentContext
) -> dict[str, Any]:
    """Manually re-run objection extraction for a conversation. Useful during tuning."""
    if ctx.merchant_id is None:
        raise PermissionDeniedError("Requires merchant context", error_code="no_merchant_context")
    arq = request.app.state.arq
    await arq.enqueue_job(
        "objection_extraction",
        str(conversation_id),
        _job_id=f"obj:extract:{conversation_id}",
    )
    return {"enqueued": True, "conversation_id": str(conversation_id)}
