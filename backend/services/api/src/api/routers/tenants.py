"""Tenant CRUD endpoints.

Access split:

- Read/patch: `agency_admin` / `agency_user` see and edit their own tenant
  (RLS handles the filtering — migration 0004 wraps the JWT claim in an
  init-plan so these queries stay fast).
- `POST /` + cross-tenant patch: `super_admin` only. The super_admin policy
  added in migration 0005 is what lets the INSERT WITH CHECK pass; regular
  callers trip the tenant_isolation policy and fail.

Super_admin bootstrap (the chicken/egg of creating the first super_admin)
is handled by `scripts/bootstrap_super_admin.py` — not by this router.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from api.dependencies.auth import require_role
from api.dependencies.session import CurrentContext, DBSession
from db import TenantRepository
from shared import (
    SUPER_ADMIN_ROLE,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    get_logger,
)

router = APIRouter(
    dependencies=[Depends(require_role("super_admin", "agency_admin", "agency_user"))]
)
logger = get_logger(__name__)


class TenantOut(BaseModel):
    id: UUID
    slug: str
    name: str
    status: str
    settings: dict[str, Any]


class TenantCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=200)
    settings: dict[str, Any] | None = None


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    settings: dict[str, Any] | None = None


@router.get("/", response_model=list[TenantOut])
async def list_tenants(ctx: CurrentContext, session: DBSession) -> list[TenantOut]:
    tenants = await TenantRepository(session).list_visible()
    return [_to_out(t) for t in tenants]


@router.get("/me", response_model=TenantOut)
async def get_my_tenant(ctx: CurrentContext, session: DBSession) -> TenantOut:
    tenant = await TenantRepository(session).get(ctx.tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found", tenant_id=str(ctx.tenant_id))
    return _to_out(tenant)


@router.post(
    "/",
    response_model=TenantOut,
    status_code=201,
    dependencies=[Depends(require_role("super_admin"))],
)
async def create_tenant(
    payload: TenantCreate, ctx: CurrentContext, session: DBSession
) -> TenantOut:
    repo = TenantRepository(session)
    existing = await repo.get_by_slug(payload.slug)
    if existing is not None:
        raise ConflictError(
            f"Tenant slug '{payload.slug}' already exists",
            error_code="tenant_slug_exists",
        )
    try:
        tenant = await repo.create(
            slug=payload.slug,
            name=payload.name,
            settings=payload.settings or {},
        )
    except IntegrityError as e:
        raise ConflictError(
            f"Tenant slug '{payload.slug}' already exists",
            error_code="tenant_slug_exists",
        ) from e

    logger.info(
        "tenants.created",
        actor_id=str(ctx.actor_id),
        tenant_id=str(tenant.id),
        slug=tenant.slug,
    )
    return _to_out(tenant)


@router.patch(
    "/{tenant_id}",
    response_model=TenantOut,
    dependencies=[Depends(require_role("super_admin", "agency_admin"))],
)
async def update_tenant(
    tenant_id: UUID,
    payload: TenantUpdate,
    ctx: CurrentContext,
    session: DBSession,
) -> TenantOut:
    if ctx.role != SUPER_ADMIN_ROLE and tenant_id != ctx.tenant_id:
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
        super_admin=ctx.role == SUPER_ADMIN_ROLE,
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
