from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, uuid_pk


class Integration(Base, TimestampMixin):
    """External provider credentials, per merchant. `secret_ciphertext` is AES-256-GCM."""

    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint("merchant_id", "provider", name="uq_integrations_merchant_provider"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # ghl | whatsapp
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    external_account_id: Mapped[str | None] = mapped_column(String(200))
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_aad: Mapped[bytes | None] = mapped_column(LargeBinary)
    kek_version: Mapped[int] = mapped_column(nullable=False, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
