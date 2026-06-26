"""GoHighLevel marketplace credentials — agency install + per-location tokens.

Two cardinalities, two tables (see ADR 0007):

  * `ghl_agency_installs` — one row per (tenant, companyId). Holds the Company
    (Agency-level) OAuth token and the `company_id -> tenant` mapping. Tenant-scoped.
  * `ghl_location_tokens` — one row per installed GHL `locationId`. Holds the
    minted Location-level token. A row may exist *before* it is linked to a
    merchant (status `pending_link`, `merchant_id` NULL), so `merchant_id` is
    nullable and `tenant_id` is denormalised for RLS + webhook lookup.

`secret_ciphertext` is AES-256-GCM over the JSON bundle
`{"access_token","refresh_token","expires_at"}`, same scheme as `integrations`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, uuid_pk


class GHLAgencyInstall(Base, TimestampMixin):
    """Agency-level (Company) GHL token, one per tenant."""

    __tablename__ = "ghl_agency_installs"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_ghl_agency_installs_company_id"),
        UniqueConstraint("tenant_id", name="uq_ghl_agency_installs_tenant_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_aad: Mapped[bytes | None] = mapped_column(LargeBinary)
    kek_version: Mapped[int] = mapped_column(nullable=False, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    company_name: Mapped[str | None] = mapped_column(String(200))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class GHLLocationToken(Base, TimestampMixin):
    """Location-level (Sub-Account) GHL token, one per installed locationId."""

    __tablename__ = "ghl_location_tokens"
    __table_args__ = (UniqueConstraint("location_id", name="uq_ghl_location_tokens_location_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NULL until an agency admin links the location to a merchant.
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    company_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_link")
    # NULL until the location token is minted from the agency token.
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_aad: Mapped[bytes | None] = mapped_column(LargeBinary)
    kek_version: Mapped[int] = mapped_column(nullable=False, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location_name: Mapped[str | None] = mapped_column(String(200))
    installed_by_user_id: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class GhlSyncLog(Base):
    """Append-only log of every API call made to GoHighLevel.

    One row per GHL operation (contact.upserted, booking.created, etc.).
    Gives operators full visibility into what the platform sent to GHL,
    including errors and the relevant GHL entity IDs for cross-referencing.
    """

    __tablename__ = "ghl_sync_log"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    # e.g. "contact.upserted", "opportunity.created", "booking.created", etc.
    operation: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # e.g. "contact", "opportunity", "appointment", "note"
    ghl_entity_type: Mapped[str | None] = mapped_column(String(32))
    # GHL-assigned ID of the created/updated entity
    ghl_entity_id: Mapped[str | None] = mapped_column(String(128))
    # "success" | "error" | "skipped"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    error_detail: Mapped[str | None] = mapped_column(Text)
    # Sanitised payload sent (no tokens/secrets)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Truncated GHL response (id + status fields only)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        index=True,
    )
