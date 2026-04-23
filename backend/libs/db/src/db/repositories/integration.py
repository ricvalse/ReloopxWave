"""Integration resolvers — decrypt per-merchant credentials for external providers.

Lives in the db layer because the lookup is a direct query we want callable
from both workers (no JWT) and JWT-authenticated request paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Integration, Merchant
from shared import EncryptedSecret, decrypt_secret, encrypt_secret


@dataclass(slots=True, frozen=True)
class ResolvedWhatsAppIntegration:
    merchant_id: UUID
    tenant_id: UUID
    phone_number_id: str
    access_token: str  # decrypted
    meta: dict[str, Any]


@dataclass(slots=True, frozen=True)
class ResolvedGHLIntegration:
    """GHL OAuth bundle decrypted from the integrations table.

    `secret_ciphertext` holds a JSON blob
    `{"access_token", "refresh_token", "expires_at", "location_id"}`
    so a single AES-GCM payload covers the whole token bundle and its rotation
    metadata atomically.
    """

    merchant_id: UUID
    tenant_id: UUID
    access_token: str
    refresh_token: str
    expires_at: int
    location_id: str | None
    meta: dict[str, Any]


class IntegrationRepository:
    def __init__(self, session: AsyncSession, *, kek_base64: str) -> None:
        self._session = session
        self._kek = kek_base64

    async def resolve_whatsapp(self, phone_number_id: str) -> ResolvedWhatsAppIntegration | None:
        stmt = (
            select(Integration, Merchant.tenant_id)
            .join(Merchant, Merchant.id == Integration.merchant_id)
            .where(
                Integration.provider == "whatsapp",
                Integration.meta["phone_number_id"].astext == phone_number_id,
                Integration.status == "active",
            )
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        integration, tenant_id = row
        return ResolvedWhatsAppIntegration(
            merchant_id=integration.merchant_id,
            tenant_id=tenant_id,
            phone_number_id=phone_number_id,
            access_token=self._decrypt(integration),
            meta=dict(integration.meta or {}),
        )

    async def resolve_ghl(self, merchant_id: UUID) -> ResolvedGHLIntegration | None:
        stmt = (
            select(Integration, Merchant.tenant_id)
            .join(Merchant, Merchant.id == Integration.merchant_id)
            .where(
                Integration.merchant_id == merchant_id,
                Integration.provider == "ghl",
                Integration.status == "active",
            )
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        integration, tenant_id = row
        bundle = json.loads(self._decrypt(integration))
        return ResolvedGHLIntegration(
            merchant_id=integration.merchant_id,
            tenant_id=tenant_id,
            access_token=bundle["access_token"],
            refresh_token=bundle["refresh_token"],
            expires_at=int(bundle.get("expires_at", 0)),
            location_id=bundle.get("location_id"),
            meta=dict(integration.meta or {}),
        )

    def _decrypt(self, integration: Integration) -> str:
        return decrypt_secret(
            EncryptedSecret(
                ciphertext=bytes(integration.secret_ciphertext),
                nonce=bytes(integration.secret_nonce),
                aad=bytes(integration.secret_aad) if integration.secret_aad else None,
                kek_version=integration.kek_version,
            ),
            kek_base64=self._kek,
        )

    # ---- Writes ----------------------------------------------------------

    async def upsert_ghl(
        self,
        *,
        merchant_id: UUID,
        access_token: str,
        refresh_token: str,
        expires_at: int,
        location_id: str | None,
        extra_meta: dict[str, Any] | None = None,
    ) -> Integration:
        """Encrypt the whole GHL token bundle as a single AES-GCM blob and upsert
        the `(merchant_id, provider='ghl')` row. Resolver reads expect this JSON
        shape, so don't split fields across columns.
        """
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": int(expires_at),
            "location_id": location_id,
        }
        aad = f"ghl:{merchant_id}".encode()
        secret = encrypt_secret(json.dumps(payload), kek_base64=self._kek, aad=aad)

        integration = await self._get("ghl", merchant_id)
        expires_ts = datetime.fromtimestamp(expires_at, tz=UTC) if expires_at else None
        meta = {**(integration.meta if integration else {}), **(extra_meta or {})}
        if location_id:
            meta["location_id"] = location_id

        if integration is None:
            integration = Integration(
                merchant_id=merchant_id,
                provider="ghl",
                status="active",
                external_account_id=location_id,
                secret_ciphertext=secret.ciphertext,
                secret_nonce=secret.nonce,
                secret_aad=secret.aad,
                kek_version=secret.kek_version,
                expires_at=expires_ts,
                meta=meta,
            )
            self._session.add(integration)
        else:
            integration.status = "active"
            integration.external_account_id = location_id
            integration.secret_ciphertext = secret.ciphertext
            integration.secret_nonce = secret.nonce
            integration.secret_aad = secret.aad
            integration.kek_version = secret.kek_version
            integration.expires_at = expires_ts
            integration.meta = meta

        await self._session.flush()
        return integration

    async def upsert_whatsapp(
        self,
        *,
        merchant_id: UUID,
        phone_number_id: str,
        access_token: str,
        display_phone: str | None = None,
    ) -> Integration:
        aad = f"wa:{merchant_id}".encode()
        secret = encrypt_secret(access_token, kek_base64=self._kek, aad=aad)

        integration = await self._get("whatsapp", merchant_id)
        meta: dict[str, Any] = {
            **(integration.meta if integration else {}),
            "phone_number_id": phone_number_id,
        }
        if display_phone:
            meta["display_phone"] = display_phone

        if integration is None:
            integration = Integration(
                merchant_id=merchant_id,
                provider="whatsapp",
                status="active",
                external_account_id=phone_number_id,
                secret_ciphertext=secret.ciphertext,
                secret_nonce=secret.nonce,
                secret_aad=secret.aad,
                kek_version=secret.kek_version,
                meta=meta,
            )
            self._session.add(integration)
        else:
            integration.status = "active"
            integration.external_account_id = phone_number_id
            integration.secret_ciphertext = secret.ciphertext
            integration.secret_nonce = secret.nonce
            integration.secret_aad = secret.aad
            integration.kek_version = secret.kek_version
            integration.meta = meta

        await self._session.flush()
        return integration

    async def list_status(self, merchant_id: UUID) -> list[IntegrationStatus]:
        stmt = select(Integration).where(Integration.merchant_id == merchant_id)
        rows = (await self._session.execute(stmt)).scalars()
        return [
            IntegrationStatus(
                provider=r.provider,
                status=r.status,
                external_account_id=r.external_account_id,
                expires_at=int(r.expires_at.timestamp()) if r.expires_at else None,
                meta=dict(r.meta or {}),
            )
            for r in rows
        ]

    async def _get(self, provider: str, merchant_id: UUID) -> Integration | None:
        stmt = select(Integration).where(
            Integration.merchant_id == merchant_id,
            Integration.provider == provider,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()


@dataclass(slots=True, frozen=True)
class IntegrationStatus:
    provider: str
    status: str
    external_account_id: str | None
    expires_at: int | None
    meta: dict[str, Any]
