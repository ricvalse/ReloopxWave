"""Agency→merchant impersonation (UC-10).

`POST /admin/impersonation/{merchant_id}` lets an `agency_admin` mint a
short-lived, merchant-scoped Supabase access token for one of *its own*
merchants. The frontend hands the token to web-merchant, which then runs the
merchant portal 1:1 as that merchant. The session is non-renewable and audited.

Security: role-gated to `agency_admin`, the target merchant must belong to the
caller's tenant, and every mint is logged with the `actor_id` (security
invariant in CLAUDE.md — service-role-adjacent admin actions are auditable).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.dependencies.auth import require_role
from api.dependencies.session import CurrentContext, DBSession
from db import MerchantRepository, UserRepository
from integrations import mint_impersonation_token
from shared import NotFoundError, PermissionDeniedError, get_logger, get_settings

logger = get_logger(__name__)

router = APIRouter()

# Default impersonation window. Short because the token is stateless (no hard
# revoke before expiry). Clamped again inside the minting util.
_IMPERSONATION_TTL_SECONDS = 1200  # 20 min


class ImpersonationOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 — OAuth token_type, not a secret
    expires_at: int
    expires_in: int
    merchant_id: UUID
    tenant_id: UUID
    merchant_name: str
    session_id: UUID
    web_merchant_url: str


@router.post(
    "/{merchant_id}",
    response_model=ImpersonationOut,
    dependencies=[Depends(require_role("agency_admin"))],
)
async def impersonate_merchant(
    merchant_id: UUID,
    ctx: CurrentContext,
    session: DBSession,
) -> ImpersonationOut:
    # Guard 1: agency_admin role is enforced by the dependency above.
    # Guard 2: a merchant-scoped user must never be able to impersonate.
    if ctx.merchant_id is not None:
        raise PermissionDeniedError(
            "Merchant-scoped users cannot impersonate",
            error_code="impersonation_not_allowed",
        )

    # Guard 3: the target merchant must belong to the admin's tenant. RLS would
    # already hide cross-tenant merchants, but we check explicitly for a clear
    # 404 and defense-in-depth.
    merchant = await MerchantRepository(session).get(merchant_id)
    if merchant is None or merchant.tenant_id != ctx.tenant_id:
        raise NotFoundError("Merchant not found", merchant_id=str(merchant_id))

    admin = await UserRepository(session).get(ctx.actor_id)
    admin_email = admin.email if admin is not None else None

    settings = get_settings()
    token = mint_impersonation_token(
        jwt_secret=settings.supabase_jwt_secret,
        supabase_url=settings.supabase_url,
        admin_user_id=ctx.actor_id,
        admin_email=admin_email,
        tenant_id=ctx.tenant_id,
        merchant_id=merchant.id,
        merchant_name=merchant.name,
        ttl_seconds=_IMPERSONATION_TTL_SECONDS,
    )

    logger.info(
        "impersonation.session.minted",
        actor_id=str(ctx.actor_id),
        tenant_id=str(ctx.tenant_id),
        target_merchant_id=str(merchant.id),
        target_merchant_status=merchant.status,
        session_id=str(token.session_id),
        expires_at=token.expires_at,
    )

    return ImpersonationOut(
        access_token=token.access_token,
        expires_at=token.expires_at,
        expires_in=token.expires_in,
        merchant_id=merchant.id,
        tenant_id=merchant.tenant_id,
        merchant_name=merchant.name,
        session_id=token.session_id,
        web_merchant_url=settings.public_web_merchant_url.rstrip("/"),
    )
