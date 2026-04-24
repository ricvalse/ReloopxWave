"""Tenant read/patch endpoints.

There's exactly one tenant in this deployment — Wave Marketing itself —
seeded by `POST /auth/bootstrap` on first admin login. The router only
exposes read + patch so the admin can rename the tenant or tweak its
settings; creation and listing-of-many intentionally aren't exposed.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies.auth import require_role
from api.dependencies.session import CurrentContext, DBSession
from db import TenantRepository
from shared import (
    NotFoundError,
    PermissionDeniedError,
    get_logger,
)

router = APIRouter(dependencies=[Depends(require_role("agency_admin"))])
logger = get_logger(__name__)


class TenantOut(BaseModel):
    id: UUID
    slug: str
    name: str
    status: str
    settings: dict[str, Any]


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    settings: dict[str, Any] | None = None


@router.get("/me", response_model=TenantOut)
async def get_my_tenant(ctx: CurrentContext, session: DBSession) -> TenantOut:
    tenant = await TenantRepository(session).get(ctx.tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found", tenant_id=str(ctx.tenant_id))
    return _to_out(tenant)


@router.patch("/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: UUID,
    payload: TenantUpdate,
    ctx: CurrentContext,
    session: DBSession,
) -> TenantOut:
    if tenant_id != ctx.tenant_id:
        raise PermissionDeniedError(
            "Cannot update another tenant",
            error_code="cross_tenant_update",
        )
    repo = TenantRepository(session)
    updated = await repo.update(
        tenant_id,
        name=payload.name,
        settings=payload.settings,
    )
    if updated is None:
        raise NotFoundError("Tenant not found", tenant_id=str(tenant_id))
    logger.info(
        "tenants.updated",
        actor_id=str(ctx.actor_id),
        tenant_id=str(tenant_id),
    )
    return _to_out(updated)


def _to_out(t: Any) -> TenantOut:
    return TenantOut(
        id=t.id,
        slug=t.slug,
        name=t.name,
        status=t.status,
        settings=t.settings or {},
    )
