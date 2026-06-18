"""GHL marketplace resolvers — agency installs + per-location tokens.

Callable from both the OAuth callback / INSTALL worker (no JWT, service-role
`session_scope()`) and JWT-authenticated request paths (web-admin linking UI).
The encryption scheme matches `IntegrationRepository`: a single AES-256-GCM blob
over `{"access_token","refresh_token","expires_at"}`, AAD bound to the subject.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import GHLAgencyInstall, GHLLocationToken
from shared import EncryptedSecret, decrypt_secret, encrypt_secret


@dataclass(slots=True, frozen=True)
class ResolvedAgencyInstall:
    tenant_id: UUID
    company_id: str
    access_token: str
    refresh_token: str
    expires_at: int
    status: str
    company_name: str | None


@dataclass(slots=True, frozen=True)
class ResolvedLocationToken:
    merchant_id: UUID | None
    tenant_id: UUID
    company_id: str
    location_id: str
    access_token: str
    refresh_token: str
    expires_at: int
    status: str
    meta: dict[str, Any]


@dataclass(slots=True, frozen=True)
class GHLLocationSummary:
    location_id: str
    location_name: str | None
    status: str
    merchant_id: UUID | None
    company_id: str
    expires_at: int | None


def _agency_aad(tenant_id: UUID, company_id: str) -> bytes:
    return f"ghl_agency:{tenant_id}:{company_id}".encode()


def _location_aad(location_id: str) -> bytes:
    return f"ghl_location:{location_id}".encode()


def _token_payload(access_token: str, refresh_token: str, expires_at: int) -> str:
    return json.dumps(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": int(expires_at),
        }
    )


def _expires_ts(expires_at: int) -> datetime | None:
    return datetime.fromtimestamp(expires_at, tz=UTC) if expires_at else None


class GHLMarketplaceRepository:
    def __init__(self, session: AsyncSession, *, kek_base64: str) -> None:
        self._session = session
        self._kek = kek_base64

    # ---- Agency install -------------------------------------------------

    async def upsert_agency_install(
        self,
        *,
        tenant_id: UUID,
        company_id: str,
        access_token: str,
        refresh_token: str,
        expires_at: int,
        company_name: str | None = None,
    ) -> GHLAgencyInstall:
        secret = encrypt_secret(
            _token_payload(access_token, refresh_token, expires_at),
            kek_base64=self._kek,
            aad=_agency_aad(tenant_id, company_id),
        )
        existing = (
            await self._session.execute(
                select(GHLAgencyInstall).where(GHLAgencyInstall.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()

        if existing is None:
            install = GHLAgencyInstall(
                tenant_id=tenant_id,
                company_id=company_id,
                status="active",
                secret_ciphertext=secret.ciphertext,
                secret_nonce=secret.nonce,
                secret_aad=secret.aad,
                kek_version=secret.kek_version,
                expires_at=_expires_ts(expires_at),
                company_name=company_name,
            )
            self._session.add(install)
        else:
            existing.company_id = company_id
            existing.status = "active"
            existing.secret_ciphertext = secret.ciphertext
            existing.secret_nonce = secret.nonce
            existing.secret_aad = secret.aad
            existing.kek_version = secret.kek_version
            existing.expires_at = _expires_ts(expires_at)
            if company_name:
                existing.company_name = company_name
            install = existing

        await self._session.flush()
        return install

    async def resolve_agency_by_company_id(self, company_id: str) -> ResolvedAgencyInstall | None:
        stmt = select(GHLAgencyInstall).where(
            GHLAgencyInstall.company_id == company_id,
            GHLAgencyInstall.status == "active",
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_resolved_agency(row)

    async def resolve_agency_by_tenant(self, tenant_id: UUID) -> ResolvedAgencyInstall | None:
        stmt = select(GHLAgencyInstall).where(
            GHLAgencyInstall.tenant_id == tenant_id,
            GHLAgencyInstall.status == "active",
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_resolved_agency(row)

    def _to_resolved_agency(self, row: GHLAgencyInstall | None) -> ResolvedAgencyInstall | None:
        if row is None:
            return None
        bundle = json.loads(
            decrypt_secret(
                EncryptedSecret(
                    ciphertext=bytes(row.secret_ciphertext),
                    nonce=bytes(row.secret_nonce),
                    aad=bytes(row.secret_aad) if row.secret_aad else None,
                    kek_version=row.kek_version,
                ),
                kek_base64=self._kek,
            )
        )
        return ResolvedAgencyInstall(
            tenant_id=row.tenant_id,
            company_id=row.company_id,
            access_token=bundle["access_token"],
            refresh_token=bundle["refresh_token"],
            expires_at=int(bundle.get("expires_at", 0)),
            status=row.status,
            company_name=row.company_name,
        )

    # ---- Location tokens ------------------------------------------------

    async def upsert_location_install(
        self,
        *,
        tenant_id: UUID,
        company_id: str,
        location_id: str,
        location_name: str | None = None,
        installed_by_user_id: str | None = None,
    ) -> GHLLocationToken:
        """Idempotent on `location_id`. Creates a `pending_link` row, or revives a
        revoked one, without clobbering an existing merchant link."""
        existing = await self._get_location(location_id)
        if existing is None:
            row = GHLLocationToken(
                tenant_id=tenant_id,
                company_id=company_id,
                location_id=location_id,
                status="pending_link",
                location_name=location_name,
                installed_by_user_id=installed_by_user_id,
            )
            self._session.add(row)
        else:
            existing.tenant_id = tenant_id
            existing.company_id = company_id
            if location_name:
                existing.location_name = location_name
            if installed_by_user_id:
                existing.installed_by_user_id = installed_by_user_id
            if existing.status == "revoked":
                existing.status = "pending_link"
            row = existing
        await self._session.flush()
        return row

    async def set_location_token(
        self,
        *,
        location_id: str,
        access_token: str,
        refresh_token: str,
        expires_at: int,
    ) -> GHLLocationToken | None:
        """Encrypt + store a minted/rotated location token. Promotes the row to
        `active` once it is both tokened and linked to a merchant."""
        row = await self._get_location(location_id)
        if row is None:
            return None
        secret = encrypt_secret(
            _token_payload(access_token, refresh_token, expires_at),
            kek_base64=self._kek,
            aad=_location_aad(location_id),
        )
        row.secret_ciphertext = secret.ciphertext
        row.secret_nonce = secret.nonce
        row.secret_aad = secret.aad
        row.kek_version = secret.kek_version
        row.expires_at = _expires_ts(expires_at)
        if row.status != "revoked":
            row.status = "active" if row.merchant_id is not None else "pending_link"
        await self._session.flush()
        return row

    async def link_location(self, *, location_id: str, merchant_id: UUID) -> bool:
        row = await self._get_location(location_id)
        if row is None:
            return False
        row.merchant_id = merchant_id
        if row.secret_ciphertext is not None and row.status != "revoked":
            row.status = "active"
        await self._session.flush()
        return True

    async def unlink_location(self, *, location_id: str) -> bool:
        row = await self._get_location(location_id)
        if row is None:
            return False
        row.merchant_id = None
        if row.status == "active":
            row.status = "pending_link"
        await self._session.flush()
        return True

    async def revoke_location(self, location_id: str) -> bool:
        stmt = (
            update(GHLLocationToken)
            .where(GHLLocationToken.location_id == location_id)
            .values(
                status="revoked",
                secret_ciphertext=None,
                secret_nonce=None,
                secret_aad=None,
                expires_at=None,
            )
        )
        result = cast(CursorResult[Any], await self._session.execute(stmt))
        await self._session.flush()
        return (result.rowcount or 0) > 0

    async def resolve_location_by_id(self, location_id: str) -> ResolvedLocationToken | None:
        row = await self._get_location(location_id)
        if row is None or row.status != "active":
            return None
        return self._to_resolved_location(row)

    async def merchant_id_for_location(self, location_id: str) -> UUID | None:
        """Lightweight locationId -> merchant_id lookup (no token decrypt).

        Used to route marketplace data webhooks (which carry `locationId`, not
        our merchant id) to the right merchant. Returns None for unknown or
        not-yet-linked locations."""
        row = await self._get_location(location_id)
        return row.merchant_id if row is not None else None

    async def resolve_location_by_merchant(self, merchant_id: UUID) -> ResolvedLocationToken | None:
        stmt = select(GHLLocationToken).where(
            GHLLocationToken.merchant_id == merchant_id,
            GHLLocationToken.status == "active",
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return self._to_resolved_location(row)

    async def list_active_linked_locations(self) -> list[ResolvedLocationToken]:
        """Cross-tenant: every active location token linked to a merchant, with
        its decrypted bundle. Used by the appointment reconcile poll (UC-02),
        which runs without a JWT under the service-role `session_scope()`."""
        stmt = select(GHLLocationToken).where(
            GHLLocationToken.status == "active",
            GHLLocationToken.merchant_id.is_not(None),
        )
        rows = (await self._session.execute(stmt)).scalars()
        out: list[ResolvedLocationToken] = []
        for row in rows:
            resolved = self._to_resolved_location(row)
            if resolved is not None:
                out.append(resolved)
        return out

    async def resolve_location_summary_by_merchant(
        self, merchant_id: UUID
    ) -> GHLLocationSummary | None:
        """Non-decrypting status lookup for a merchant's linked location."""
        stmt = (
            select(GHLLocationToken)
            .where(GHLLocationToken.merchant_id == merchant_id)
            .order_by(GHLLocationToken.created_at)
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return GHLLocationSummary(
            location_id=row.location_id,
            location_name=row.location_name,
            status=row.status,
            merchant_id=row.merchant_id,
            company_id=row.company_id,
            expires_at=int(row.expires_at.timestamp()) if row.expires_at else None,
        )

    async def list_locations(self, tenant_id: UUID) -> list[GHLLocationSummary]:
        stmt = (
            select(GHLLocationToken)
            .where(GHLLocationToken.tenant_id == tenant_id)
            .order_by(GHLLocationToken.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [
            GHLLocationSummary(
                location_id=r.location_id,
                location_name=r.location_name,
                status=r.status,
                merchant_id=r.merchant_id,
                company_id=r.company_id,
                expires_at=int(r.expires_at.timestamp()) if r.expires_at else None,
            )
            for r in rows
        ]

    def _to_resolved_location(self, row: GHLLocationToken) -> ResolvedLocationToken | None:
        if row.secret_ciphertext is None or row.secret_nonce is None:
            return None
        bundle = json.loads(
            decrypt_secret(
                EncryptedSecret(
                    ciphertext=bytes(row.secret_ciphertext),
                    nonce=bytes(row.secret_nonce),
                    aad=bytes(row.secret_aad) if row.secret_aad else None,
                    kek_version=row.kek_version,
                ),
                kek_base64=self._kek,
            )
        )
        return ResolvedLocationToken(
            merchant_id=row.merchant_id,
            tenant_id=row.tenant_id,
            company_id=row.company_id,
            location_id=row.location_id,
            access_token=bundle["access_token"],
            refresh_token=bundle["refresh_token"],
            expires_at=int(bundle.get("expires_at", 0)),
            status=row.status,
            meta=dict(row.meta or {}),
        )

    async def _get_location(self, location_id: str) -> GHLLocationToken | None:
        stmt = select(GHLLocationToken).where(GHLLocationToken.location_id == location_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()
