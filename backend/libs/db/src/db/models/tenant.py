from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from db.models.bot import BotConfig, BotTemplate
    from db.models.conversation import Conversation
    from db.models.lead import Lead


class Tenant(Base, TimestampMixin):
    """Agency-level tenant (the primary tenant)."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    merchants: Mapped[list[Merchant]] = relationship(back_populates="tenant")
    templates: Mapped[list[BotTemplate]] = relationship(back_populates="tenant")


class Merchant(Base, TimestampMixin):
    """Sub-tenant scoped to a Tenant."""

    __tablename__ = "merchants"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_merchants_tenant_slug"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Rome")
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="it")

    tenant: Mapped[Tenant] = relationship(back_populates="merchants")
    bot_config: Mapped[BotConfig | None] = relationship(back_populates="merchant", uselist=False)
    conversations: Mapped[list[Conversation]] = relationship(back_populates="merchant")
    leads: Mapped[list[Lead]] = relationship(back_populates="merchant")


class User(Base, TimestampMixin):
    """Extends Supabase auth.users with app-level role and tenancy claims.

    We mirror the Supabase user id here so FKs can target it without a cross-schema
    reference. The custom claims (tenant_id, merchant_id, role) are also written
    into the JWT via a Supabase Auth hook.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
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
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
