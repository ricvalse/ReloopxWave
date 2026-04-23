"""Tenant CRUD endpoints.

V1 scope notes:
- Tenant *creation* (POST /tenants/) is deliberately out of scope here: new
  tenants are provisioned out-of-band (migration seed or Supabase SQL) and the
  first login is bootstrapped by attaching the caller to the seeded tenant via
  `users.tenant_id` + the Supabase Auth JWT hook. A dedicated bootstrap route
  tracked in Phase 1.2 of the implementation plan.
- Under RLS the `tenants` table only exposes the caller's own row, so
  "list" returns a single-element list. The endpoint remains plural for API
  uniformity and future super-admin use.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies.auth import require_role
from api.dependencies.session import CurrentContext, DBSession
from db import TenantRepository
from shared import DomainError, NotFoundError, PermissionDeniedError

router = APIRouter(dependencies=[Depends(require_role("agency_admin", "agency_user"))])


class TenantOut(BaseModel):
    id: UUID
    slug: str
    name: str
    status: str
    settings: dict[str, Any]


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


@router.patch(
    "/{tenant_id}",
    response_model=TenantOut,
    dependencies=[Depends(require_role("agency_admin"))],
)
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
    return _to_out(updated)


@router.post("/", dependencies=[Depends(require_role("agency_admin"))])
async def create_tenant() -> dict[str, Any]:
    raise _NotImplementedError(
        "Tenant creation via API is disabled in V1 — provision tenants via "
        "SQL seed or Supabase admin. Tracked as the bootstrap route in Phase 1.2.",
        error_code="tenant_creation_disabled",
    )


def _to_out(t: Any) -> TenantOut:
    return TenantOut(
        id=t.id,
        slug=t.slug,
        name=t.name,
        status=t.status,
        settings=t.settings or {},
    )


class _NotImplementedError(DomainError):
    """501 so callers can distinguish 'not allowed' from 'not built yet'."""

    status_code = 501
    error_code = "not_implemented"
