"""Auth endpoints — whoami + one-shot bootstrap for the Wave admin.

`POST /auth/bootstrap` replaces the super_admin provisioning script. It's how
the very first login on web-admin turns into an `agency_admin` with a real
tenant row — a chicken-and-egg problem the original design punted to a CLI
script that needed the service_role key on the operator's laptop.

Safety properties:
  1. Only runs when *zero* tenants exist yet. Once Wave Marketing is seeded,
     the endpoint returns 409 to any future caller who isn't already the
     bootstrapped admin.
  2. Idempotent for the admin who bootstrapped: calling it again with their
     (post-refresh) JWT returns 200 with the existing tenant so the frontend
     can retry without side effects.
  3. Never grants merchant_id — the boostrap only mints agency_admin claims.
     Merchant users continue to come through the regular invite flow.
"""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from api.dependencies.auth import verify_supabase_jwt
from api.dependencies.session import CurrentContext
from db import session_scope, tenant_session
from db.models import Tenant, User
from db.session import TenantContext
from integrations import SupabaseAdminClient
from shared import ConflictError, get_logger, get_settings

router = APIRouter()
logger = get_logger(__name__)

WAVE_TENANT_SLUG = "wave"
WAVE_TENANT_NAME = "Wave Marketing"
AGENCY_ADMIN_ROLE = "agency_admin"


class BootstrapStatusOut(BaseModel):
    available: bool
    tenant_id: UUID | None


class BootstrapOut(BaseModel):
    tenant_id: UUID
    tenant_slug: str
    role: str
    created: bool
    requires_reauth: bool


@router.get("/whoami")
async def whoami(ctx: CurrentContext) -> dict[str, Any]:
    return {
        "actor_id": str(ctx.actor_id),
        "tenant_id": str(ctx.tenant_id),
        "merchant_id": str(ctx.merchant_id) if ctx.merchant_id else None,
        "role": ctx.role,
    }


@router.get("/bootstrap/status", response_model=BootstrapStatusOut)
async def bootstrap_status() -> BootstrapStatusOut:
    """Tell the frontend whether the deployment still needs a first-admin.

    Runs unauthenticated. We use a raw SQL probe that sidesteps RLS via the
    `postgres` connection role. If the Wave tenant row is present, bootstrap
    is closed; otherwise the login page can offer the "create first admin"
    action to the very next person who signs up.
    """
    tenant_id = await _find_wave_tenant_id()
    return BootstrapStatusOut(available=tenant_id is None, tenant_id=tenant_id)


@router.post("/bootstrap", response_model=BootstrapOut)
async def bootstrap(
    claims: Annotated[dict[str, Any], Depends(verify_supabase_jwt)],
) -> BootstrapOut:
    user_id = UUID(str(claims["sub"]))
    email = str(claims.get("email") or "").lower()

    claim_tenant = claims.get("tenant_id")
    claim_role = claims.get("role")

    # Case 1 — caller already carries admin claims. Confirm the tenant row
    # exists (refresh race) and return idempotently.
    if claim_tenant and claim_role == AGENCY_ADMIN_ROLE:
        tenant_id = UUID(str(claim_tenant))
        tenant = await _get_tenant(tenant_id)
        if tenant is None:
            raise ConflictError(
                "Tenant referenced in JWT no longer exists",
                error_code="stale_tenant_claim",
            )
        return BootstrapOut(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            role=AGENCY_ADMIN_ROLE,
            created=False,
            requires_reauth=False,
        )

    # Case 2 — caller has some *other* tenant/role claim. Someone already
    # bootstrapped; this user needs a proper invite instead.
    if claim_tenant:
        raise ConflictError(
            "Deployment already bootstrapped — ask the admin for an invite",
            error_code="already_bootstrapped",
        )

    # Case 3 — fresh caller. If Wave tenant already exists, this user isn't
    # the first admin; refuse.
    existing_id = await _find_wave_tenant_id()
    if existing_id is not None:
        raise ConflictError(
            "Deployment already bootstrapped — ask the admin for an invite",
            error_code="already_bootstrapped",
        )

    # Create tenant + user row. Forge a claim that matches the new tenant_id
    # so the RLS WITH CHECK on the tenants policy passes. `role=agency_admin`
    # also satisfies the user-table policy since merchant_id is null.
    new_tenant_id = uuid4()
    forged_ctx = TenantContext(
        tenant_id=new_tenant_id,
        merchant_id=None,
        role=AGENCY_ADMIN_ROLE,
        actor_id=user_id,
    )

    async with tenant_session(forged_ctx) as session:
        tenant = Tenant(
            id=new_tenant_id,
            slug=WAVE_TENANT_SLUG,
            name=WAVE_TENANT_NAME,
            status="active",
            settings={},
        )
        session.add(tenant)
        user_row = User(
            id=user_id,
            email=email,
            tenant_id=new_tenant_id,
            merchant_id=None,
            role=AGENCY_ADMIN_ROLE,
            full_name=None,
        )
        session.add(user_row)
        await session.flush()

    # Promote the Supabase auth user so the JWT hook mints the claims on the
    # next token refresh. Without this step the caller's JWT stays claimless.
    settings = get_settings()
    admin = SupabaseAdminClient(
        supabase_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )
    try:
        await admin.set_app_metadata(
            user_id=user_id,
            tenant_id=new_tenant_id,
            merchant_id=None,
            role=AGENCY_ADMIN_ROLE,
        )
    finally:
        await admin.close()

    logger.info(
        "auth.bootstrap.completed",
        actor_id=str(user_id),
        tenant_id=str(new_tenant_id),
        email=email,
    )
    return BootstrapOut(
        tenant_id=new_tenant_id,
        tenant_slug=WAVE_TENANT_SLUG,
        role=AGENCY_ADMIN_ROLE,
        created=True,
        requires_reauth=True,
    )


# ---- helpers --------------------------------------------------------------


async def _find_wave_tenant_id() -> UUID | None:
    """Probe for the Wave tenant without any JWT claim set.

    Runs under `session_scope` which doesn't forge a claim. The Supabase
    pooler connection is a superuser, so RLS doesn't hide rows here — good
    enough for a single-row existence check on a singleton table.
    """
    async with session_scope() as session:
        stmt = select(Tenant.id).where(Tenant.slug == WAVE_TENANT_SLUG).limit(1)
        return (await session.execute(stmt)).scalar_one_or_none()


async def _get_tenant(tenant_id: UUID) -> Tenant | None:
    async with session_scope() as session:
        return await session.get(Tenant, tenant_id)
