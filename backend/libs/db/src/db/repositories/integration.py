"""Integration resolvers — decrypt per-merchant credentials for external providers.

Lives in the db layer because the lookup is a direct query we want callable
from both workers (no JWT) and JWT-authenticated request paths.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Integration, Merchant
from shared import EncryptedSecret, decrypt_secret


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
