from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies.auth import require_role

router = APIRouter(dependencies=[Depends(require_role("agency_admin"))])


@router.get("/")
async def list_tenants() -> list[dict]:
    raise NotImplementedError("UC-12 admin tenants list")


@router.post("/")
async def create_tenant() -> dict:
    raise NotImplementedError("UC-10 admin tenant creation")
