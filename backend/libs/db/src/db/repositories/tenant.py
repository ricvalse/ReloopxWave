"""Repositories for tenants, merchants, and the app-level mirror of auth.users.

The DB schema lives in migration 0001_initial. RLS policies scope reads to the
current JWT's tenant_id / merchant_id — see the `_merchant_scoped_predicate`
block in that migration. These repositories therefore do *not* re-filter by
tenant on reads inside a JWT-scoped `tenant_session`; they rely on RLS to
filter silently. Writes do pass tenant_id explicitly because WITH CHECK needs
a value that matches the claim.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Merchant, Tenant, User


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_visible(self) -> list[Tenant]:
        """Returns every tenant the caller's JWT can see. Under normal RLS this
        is exactly one row (the caller's own tenant).
        """
        return list((await self._session.execute(select(Tenant))).scalars())

    async def get(self, tenant_id: UUID) -> Tenant | None:
        return await self._session.get(Tenant, tenant_id)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        stmt = select(Tenant).where(Tenant.slug == slug)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        slug: str,
        name: str,
        settings: dict[str, Any] | None = None,
    ) -> Tenant:
        """Insert a tenant. Only super_admin sessions can execute this — RLS
        policy `super_admin_bypass_tenants` (migration 0005) is what lets the
        WITH CHECK pass; regular callers hit the isolation policy and fail.
        """
        tenant = Tenant(slug=slug, name=name, settings=settings or {})
        self._session.add(tenant)
        await self._session.flush()
        return tenant

    async def update(
        self,
        tenant_id: UUID,
        *,
        name: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> Tenant | None:
        tenant = await self._session.get(Tenant, tenant_id)
        if tenant is None:
            return None
        if name is not None:
            tenant.name = name
        if settings is not None:
            tenant.settings = settings
        await self._session.flush()
        return tenant


class MerchantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_tenant(self, tenant_id: UUID) -> list[Merchant]:
        stmt = (
            select(Merchant)
            .where(Merchant.tenant_id == tenant_id)
            .order_by(Merchant.created_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars())

    async def get(self, merchant_id: UUID) -> Merchant | None:
        return await self._session.get(Merchant, merchant_id)

    async def create(
        self,
        *,
        tenant_id: UUID,
        slug: str,
        name: str,
        timezone: str = "Europe/Rome",
        locale: str = "it",
    ) -> Merchant:
        merchant = Merchant(
            tenant_id=tenant_id,
            slug=slug,
            name=name,
            timezone=timezone,
            locale=locale,
        )
        self._session.add(merchant)
        await self._session.flush()
        return merchant

    async def update(
        self,
        merchant_id: UUID,
        *,
        name: str | None = None,
        timezone: str | None = None,
        locale: str | None = None,
    ) -> Merchant | None:
        merchant = await self._session.get(Merchant, merchant_id)
        if merchant is None:
            return None
        if name is not None:
            merchant.name = name
        if timezone is not None:
            merchant.timezone = timezone
        if locale is not None:
            merchant.locale = locale
        await self._session.flush()
        return merchant

    async def set_status(self, merchant_id: UUID, status: str) -> Merchant | None:
        merchant = await self._session.get(Merchant, merchant_id)
        if merchant is None:
            return None
        merchant.status = status
        await self._session.flush()
        return merchant


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_scope(
        self, *, tenant_id: UUID, merchant_id: UUID | None = None
    ) -> list[User]:
        stmt = select(User).where(User.tenant_id == tenant_id)
        if merchant_id is not None:
            stmt = stmt.where(User.merchant_id == merchant_id)
        stmt = stmt.order_by(User.created_at.desc())
        return list((await self._session.execute(stmt)).scalars())

    async def get(self, user_id: UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.lower())
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self,
        *,
        user_id: UUID,
        email: str,
        tenant_id: UUID,
        merchant_id: UUID | None,
        role: str,
        full_name: str | None = None,
    ) -> User:
        existing = await self._session.get(User, user_id)
        if existing is None:
            user = User(
                id=user_id,
                email=email.lower(),
                tenant_id=tenant_id,
                merchant_id=merchant_id,
                role=role,
                full_name=full_name,
            )
            self._session.add(user)
            await self._session.flush()
            return user

        existing.email = email.lower()
        existing.tenant_id = tenant_id
        existing.merchant_id = merchant_id
        existing.role = role
        if full_name is not None:
            existing.full_name = full_name
        await self._session.flush()
        return existing
