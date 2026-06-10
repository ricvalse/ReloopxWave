"""Merchant CRUD endpoints — UC-10 (onboarding), UC-11 (merchant portal target),
UC-12 (agency dashboard list).

Every read/write is scoped to the caller's tenant twice:

1. `require_role("agency_admin")` on writes — merchant users cannot create
   or mutate their own record.
2. Postgres RLS on `merchants` filters silently via the EXISTS join on
   `merchants.tenant_id = request.jwt.claims.tenant_id` (see 0001_initial).
   A cross-tenant lookup therefore returns no rows, which we surface as 404.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

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

logger = get_logger(__name__)

router = APIRouter()


class MerchantIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(default="Europe/Rome", max_length=64)
    locale: str = Field(default="it", max_length=8)


class MerchantPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=8)


class MerchantOut(BaseModel):
    id: UUID
    tenant_id: UUID
    slug: str
    name: str
    status: str
    timezone: str
    locale: str


@router.get("/", response_model=list[MerchantOut])
async def list_merchants(ctx: CurrentContext, session: DBSession) -> list[MerchantOut]:
    merchants = await MerchantRepository(session).list_for_tenant(ctx.tenant_id)
    return [_to_out(m) for m in merchants]


@router.post(
    "/",
    response_model=MerchantOut,
    status_code=201,
    dependencies=[Depends(require_role("agency_admin"))],
)
async def create_merchant(
    payload: MerchantIn, ctx: CurrentContext, session: DBSession
) -> MerchantOut:
    repo = MerchantRepository(session)
    try:
        merchant = await repo.create(
            tenant_id=ctx.tenant_id,
            slug=payload.slug,
            name=payload.name,
            timezone=payload.timezone,
            locale=payload.locale,
        )
    except IntegrityError as e:
        raise ConflictError(
            f"Merchant slug '{payload.slug}' already exists in this tenant",
            error_code="merchant_slug_exists",
        ) from e
    return _to_out(merchant)


@router.get("/{merchant_id}", response_model=MerchantOut)
async def get_merchant(merchant_id: UUID, ctx: CurrentContext, session: DBSession) -> MerchantOut:
    merchant = await MerchantRepository(session).get(merchant_id)
    if merchant is None:
        raise NotFoundError("Merchant not found", merchant_id=str(merchant_id))
    _assert_merchant_scope(ctx, merchant)
    return _to_out(merchant)


@router.patch(
    "/{merchant_id}",
    response_model=MerchantOut,
    dependencies=[Depends(require_role("agency_admin"))],
)
async def update_merchant(
    merchant_id: UUID,
    payload: MerchantPatch,
    ctx: CurrentContext,
    session: DBSession,
) -> MerchantOut:
    repo = MerchantRepository(session)
    existing = await repo.get(merchant_id)
    if existing is None:
        raise NotFoundError("Merchant not found", merchant_id=str(merchant_id))
    _assert_merchant_scope(ctx, existing)
    updated = await repo.update(
        merchant_id,
        name=payload.name,
        timezone=payload.timezone,
        locale=payload.locale,
    )
    assert updated is not None
    return _to_out(updated)


@router.post(
    "/{merchant_id}/suspend",
    response_model=MerchantOut,
    dependencies=[Depends(require_role("agency_admin"))],
)
async def suspend_merchant(
    merchant_id: UUID, ctx: CurrentContext, session: DBSession
) -> MerchantOut:
    return await _set_status(merchant_id, ctx, session, "suspended")


@router.post(
    "/{merchant_id}/resume",
    response_model=MerchantOut,
    dependencies=[Depends(require_role("agency_admin"))],
)
async def resume_merchant(
    merchant_id: UUID, ctx: CurrentContext, session: DBSession
) -> MerchantOut:
    return await _set_status(merchant_id, ctx, session, "active")


@router.delete(
    "/{merchant_id}",
    status_code=204,
    dependencies=[Depends(require_role("agency_admin"))],
)
async def delete_merchant(merchant_id: UUID, ctx: CurrentContext, session: DBSession) -> None:
    """Hard-delete a merchant. FK cascades wipe all dependent rows
    (leads, conversations, KB, bot config, integrations, analytics).

    Also removes the merchant's auth.users entries from Supabase Auth so the
    credentials stop working — agency_admin accounts (merchant_id=NULL) are
    left untouched. Read the user list from public.users *before* the DELETE,
    because the FK CASCADE wipes the mirror in the same transaction.

    Ordering: the DB is the source of truth, so we DELETE + commit first, then
    best-effort-remove the auth users. A partial auth-cleanup failure therefore
    can't leave a half-deleted merchant (the old order deleted some auth users
    then 500'd with the merchant row still present). An orphaned auth.users row
    is inert — its public.users mirror is gone, so it can no longer resolve a
    tenant/merchant context — and is logged for reconciliation.

    Destructive — UI must require explicit confirmation.
    """
    repo = MerchantRepository(session)
    existing = await repo.get(merchant_id)
    if existing is None:
        raise NotFoundError("Merchant not found", merchant_id=str(merchant_id))
    _assert_merchant_scope(ctx, existing)

    # Capture auth targets BEFORE the delete cascades the public.users mirror.
    user_repo = UserRepository(session)
    scoped_users = await user_repo.list_for_scope(tenant_id=ctx.tenant_id, merchant_id=merchant_id)
    auth_targets = [(u.id, u.email) for u in scoped_users if u.merchant_id == merchant_id]

    # Delete and commit first — make the DB state durable before touching Auth.
    await repo.delete(merchant_id)
    await session.commit()

    settings = get_settings()
    admin = SupabaseAdminClient(
        supabase_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )
    removed = 0
    failures = 0
    try:
        for user_id, email in auth_targets:
            try:
                deleted = await admin.delete_user(user_id=user_id)
            except IntegrationError as e:
                failures += 1
                logger.error(
                    "merchant.delete.auth_user_failed",
                    actor_id=str(ctx.actor_id),
                    tenant_id=str(ctx.tenant_id),
                    merchant_id=str(merchant_id),
                    user_id=str(user_id),
                    email=email,
                    error_code=e.error_code,
                )
                continue  # best-effort: merchant is already gone, reconcile later
            removed += 1
            logger.info(
                "merchant.delete.auth_user_removed",
                actor_id=str(ctx.actor_id),
                tenant_id=str(ctx.tenant_id),
                merchant_id=str(merchant_id),
                user_id=str(user_id),
                email=email,
                already_gone=not deleted,
            )
    finally:
        await admin.close()

    logger.info(
        "merchant.deleted",
        actor_id=str(ctx.actor_id),
        tenant_id=str(ctx.tenant_id),
        merchant_id=str(merchant_id),
        merchant_slug=existing.slug,
        auth_users_removed=removed,
        auth_user_failures=failures,
    )


async def _set_status(
    merchant_id: UUID,
    ctx: CurrentContext,
    session: DBSession,
    status: str,
) -> MerchantOut:
    repo = MerchantRepository(session)
    existing = await repo.get(merchant_id)
    if existing is None:
        raise NotFoundError("Merchant not found", merchant_id=str(merchant_id))
    _assert_merchant_scope(ctx, existing)
    updated = await repo.set_status(merchant_id, status)
    assert updated is not None
    return _to_out(updated)


def _assert_merchant_scope(ctx: CurrentContext, merchant: Any) -> None:
    """RLS already filters at the DB layer; enforce tenant match again so the
    identity-map / session-cache path cannot regress into cross-tenant leakage.
    """
    if merchant.tenant_id != ctx.tenant_id:
        raise NotFoundError(
            "Merchant not found",
            merchant_id=str(merchant.id),
        )
    if ctx.merchant_id is not None and ctx.merchant_id != merchant.id:
        raise PermissionDeniedError(
            "Cannot act on another merchant",
            error_code="cross_merchant_access",
        )


def _to_out(m: Any) -> MerchantOut:
    return MerchantOut(
        id=m.id,
        tenant_id=m.tenant_id,
        slug=m.slug,
        name=m.name,
        status=m.status,
        timezone=m.timezone,
        locale=m.locale,
    )
