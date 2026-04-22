from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from api.dependencies.auth import require_role
from api.dependencies.session import CurrentContext, DBSession

router = APIRouter()


@router.get("/")
async def list_merchants(ctx: CurrentContext, session: DBSession) -> list[dict]:
    raise NotImplementedError("UC-12 agency merchant list")


@router.post("/", dependencies=[Depends(require_role("agency_admin", "agency_user"))])
async def create_merchant() -> dict:
    raise NotImplementedError("UC-10/UC-11 merchant onboarding")


@router.get("/{merchant_id}")
async def get_merchant(merchant_id: UUID, session: DBSession) -> dict:
    raise NotImplementedError("Merchant drill-down")


@router.post("/{merchant_id}/suspend", dependencies=[Depends(require_role("agency_admin"))])
async def suspend_merchant(merchant_id: UUID) -> dict:
    raise NotImplementedError("Suspend merchant")
