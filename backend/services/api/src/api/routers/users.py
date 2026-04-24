"""User listing + invite flow.

The invite endpoint is the single place where the backend legitimately calls
the Supabase service_role key (to create the auth.users row and seed its
custom claims). Every invocation is logged with `actor_id` so the audit trail
for §15 invariant-3 stays intact.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from api.dependencies.auth import require_role
from api.dependencies.session import CurrentContext, DBSession
from db import MerchantRepository, UserRepository
from integrations import SupabaseAdminClient
from shared import (
    ConflictError,
    IntegrationError,
    NotFoundError,
    PermissionDeniedError,
    get_logger,
    get_settings,
)

router = APIRouter()
logger = get_logger(__name__)

Role = Literal["agency_admin", "merchant_user"]

_MERCHANT_FILTER: Any = Query(default=None, description="Filter users by merchant_id")


class UserOut(BaseModel):
    id: UUID
    email: str
    tenant_id: UUID
    merchant_id: UUID | None
    role: str
    full_name: str | None


class InviteIn(BaseModel):
    email: str = Field(
        min_length=5,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    role: Role
    merchant_id: UUID | None = None
    full_name: str | None = Field(default=None, max_length=200)
    redirect_to: str | None = None


@router.get("/", response_model=list[UserOut])
async def list_users(
    ctx: CurrentContext,
    session: DBSession,
    merchant_id: UUID | None = _MERCHANT_FILTER,
) -> list[UserOut]:
    scope_merchant = _resolve_list_scope(ctx, merchant_id)
    repo = UserRepository(session)
    users = await repo.list_for_scope(tenant_id=ctx.tenant_id, merchant_id=scope_merchant)
    return [_to_out(u) for u in users]


@router.post(
    "/invite",
    response_model=UserOut,
    status_code=201,
    dependencies=[Depends(require_role("agency_admin"))],
)
async def invite_user(
    payload: InviteIn,
    ctx: CurrentContext,
    session: DBSession,
) -> UserOut:
    _assert_can_invite(ctx, payload)

    if payload.merchant_id is not None:
        merchant = await MerchantRepository(session).get(payload.merchant_id)
        if merchant is None or merchant.tenant_id != ctx.tenant_id:
            raise NotFoundError(
                "Merchant not found",
                merchant_id=str(payload.merchant_id),
            )

    user_repo = UserRepository(session)
    existing = await user_repo.get_by_email(payload.email)
    if existing is not None:
        raise ConflictError(
            "A user with that email already exists",
            error_code="user_email_exists",
        )

    settings = get_settings()
    client = SupabaseAdminClient(
        supabase_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )
    try:
        invited = await client.invite_user_by_email(
            email=payload.email,
            tenant_id=ctx.tenant_id,
            merchant_id=payload.merchant_id,
            role=payload.role,
            redirect_to=payload.redirect_to,
        )
    except IntegrationError:
        raise
    finally:
        await client.close()

    user = await user_repo.upsert(
        user_id=invited.id,
        email=invited.email,
        tenant_id=ctx.tenant_id,
        merchant_id=payload.merchant_id,
        role=payload.role,
        full_name=payload.full_name,
    )

    logger.info(
        "users.invite.sent",
        actor_id=str(ctx.actor_id),
        tenant_id=str(ctx.tenant_id),
        invited_user_id=str(user.id),
        invited_email=user.email,
        role=payload.role,
        merchant_id=str(payload.merchant_id) if payload.merchant_id else None,
    )
    return _to_out(user)


def _resolve_list_scope(ctx: CurrentContext, merchant_filter: UUID | None) -> UUID | None:
    """Merchant-role callers can only list within their own merchant, even if
    they pass a wider filter. Agency callers may leave it unset to see all
    users in the tenant, or filter to a specific merchant.
    """
    if ctx.merchant_id is not None:
        if merchant_filter is not None and merchant_filter != ctx.merchant_id:
            raise PermissionDeniedError(
                "Cannot list users of another merchant",
                error_code="cross_merchant_list",
            )
        return ctx.merchant_id
    return merchant_filter


def _assert_can_invite(ctx: CurrentContext, payload: InviteIn) -> None:
    if payload.role == "agency_admin":
        if payload.merchant_id is not None:
            raise PermissionDeniedError(
                "Agency admins must not be scoped to a merchant",
                error_code="invite_agency_with_merchant",
            )
        return

    # merchant_user — must target an existing merchant.
    if payload.merchant_id is None:
        raise PermissionDeniedError(
            "Merchant-role invites require merchant_id",
            error_code="invite_missing_merchant",
        )


def _to_out(u: Any) -> UserOut:
    return UserOut(
        id=u.id,
        email=u.email,
        tenant_id=u.tenant_id,
        merchant_id=u.merchant_id,
        role=u.role,
        full_name=u.full_name,
    )
