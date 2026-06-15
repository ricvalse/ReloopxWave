from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin, uuid_pk


class Flow(Base, TimestampMixin):
    """A configurable outbound sequence (scope A of the Flussi plan).

    One flow per lifecycle `key` per merchant (no_answer | reactivation |
    booking_reminder | first_contact | custom). The schedulers read the flow's
    steps instead of hardcoded copy.
    """

    __tablename__ = "flows"
    __table_args__ = (UniqueConstraint("merchant_id", "key", name="uq_flows_merchant_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    steps: Mapped[list[FlowStep]] = relationship(
        back_populates="flow",
        cascade="all, delete-orphan",
        order_by="FlowStep.step_index",
    )


class FlowStep(Base, TimestampMixin):
    """A single step in a flow: trigger delay + template binding + window policy."""

    __tablename__ = "flow_steps"
    __table_args__ = (UniqueConstraint("flow_id", "step_index", name="uq_flow_steps_flow_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    flow_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("flows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised from the parent flow so the standard merchant RLS predicate applies.
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("whatsapp_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Per-slot mapping for this step, e.g. {"1": "lead.first_name"}.
    variable_mapping: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    # auto | require_template | freeform_only — how to behave vs the 24h window.
    window_policy: Mapped[str] = mapped_column(String(24), nullable=False, default="auto")
    # Fallback copy used when inside the window (no template needed).
    free_text: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    flow: Mapped[Flow] = relationship(back_populates="steps")
