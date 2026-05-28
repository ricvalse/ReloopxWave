"""FT pipeline — trigger a run and inspect registered models (agency-admin).

POST /fine-tuning/run enqueues the orchestrator job (collect → quality →
export → train → evaluate → deploy). GET /fine-tuning/models lists the tenant's
ft_models with status + evaluation so the admin UI can show progress.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.dependencies.auth import require_role
from api.dependencies.session import CurrentContext, DBSession
from db.models import FTModel

router = APIRouter()


class RunFineTuneIn(BaseModel):
    since_days: int = Field(28, ge=7, le=120)
    base_model: str = "gpt-4.1-mini"


@router.post("/run", dependencies=[Depends(require_role("agency_admin"))])
async def run_fine_tune(
    payload: RunFineTuneIn, ctx: CurrentContext, request: Request
) -> dict[str, Any]:
    arq = request.app.state.arq
    await arq.enqueue_job(
        "fine_tune_run",
        str(ctx.tenant_id),
        since_days=payload.since_days,
        base_model=payload.base_model,
        _job_id=f"ft:run:{ctx.tenant_id}",
    )
    return {"enqueued": True, "tenant_id": str(ctx.tenant_id)}


@router.get("/models", dependencies=[Depends(require_role("agency_admin"))])
async def list_ft_models(ctx: CurrentContext, session: DBSession) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(FTModel)
            .where(FTModel.tenant_id == ctx.tenant_id)
            .order_by(FTModel.version.desc())
        )
    ).scalars()
    return [
        {
            "id": str(r.id),
            "version": r.version,
            "base_model": r.base_model,
            "provider_model_id": r.provider_model_id,
            "status": r.status,
            "is_default": r.is_default,
            "evaluation": r.evaluation or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
