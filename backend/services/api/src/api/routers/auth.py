from __future__ import annotations

from fastapi import APIRouter

from api.dependencies.session import CurrentContext

router = APIRouter()


@router.get("/whoami")
async def whoami(ctx: CurrentContext) -> dict:
    return {
        "actor_id": str(ctx.actor_id),
        "tenant_id": str(ctx.tenant_id),
        "merchant_id": str(ctx.merchant_id) if ctx.merchant_id else None,
        "role": ctx.role,
    }
