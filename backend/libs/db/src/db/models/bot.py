from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin, uuid_pk


class BotTemplate(Base, TimestampMixin):
    """Agency-level defaults (UC-10). Applied as second tier in the config cascade."""

    __tablename__ = "bot_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_bot_templates_tenant_name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    defaults: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    locked_keys: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="templates")  # type: ignore[name-defined]  # noqa: F821


class BotConfig(Base, TimestampMixin):
    """Merchant-level overrides (top of the config cascade)."""

    __tablename__ = "bot_configs"

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("bot_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    overrides: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    merchant: Mapped["Merchant"] = relationship(back_populates="bot_config")  # type: ignore[name-defined]  # noqa: F821


class PromptTemplate(Base, TimestampMixin):
    """Versioned prompts per merchant (system + variants for A/B)."""

    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "kind", "version", "variant_id", name="uq_prompt_templates_version_variant"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # system | user | escalation | ...
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    variant_id: Mapped[str] = mapped_column(String(32), nullable=False, default="default")
    body: Mapped[str] = mapped_column(String, nullable=False)
    variables: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
