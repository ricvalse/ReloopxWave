"""Automazioni — the visual "lavagnetta" automation builder (graph model).

Distinct from `Flow`/`FlowStep` (db/models/flow.py), which are the legacy
*linear* lifecycle sequences (one per fixed key: no_answer | reactivation | …).
An `AutomationFlow` is a free-form **graph** a merchant draws on a canvas:

    [trigger] ──▶ [condition] ──true──▶ [action: send template]
                              └──false─▶ [action: wait] ──▶ [action: send message]

Many automations per merchant. Nodes are `trigger | condition | action`; edges
wire them (with an optional `branch` for the two sides of a condition). The
trigger's event type is denormalised onto the flow row (`trigger_type`) so the
dispatcher can find subscribers for an event with a single indexed lookup.

`node_key` is a client-supplied stable id (e.g. "n1") referenced by edges, so
the editor can wire nodes before they have DB UUIDs; the repo replaces the whole
node/edge set on each save.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin, uuid_pk

# Node taxonomy (kept in sync with the API/router validation + the engine).
NODE_KINDS = ("trigger", "condition", "action")
TRIGGER_TYPES = (
    "message_received",  # inbound customer message
    "no_answer",  # lead went silent for trigger_config.delay_minutes
    "booking_created",  # appointment booked
    "booking_failed",  # booking attempt failed
    "lead_dormant",  # no activity for trigger_config.days
)
CONDITION_TYPES = (
    "lead_temperature",  # cfg: {"op": "==|!=", "value": "hot|warm|cold"}
    "lead_score",  # cfg: {"op": ">=|<=|>|<|==", "value": int}
    "within_24h_window",  # cfg: {} — true when the 24h service window is open
    "time_of_day",  # cfg: {"from": "09:00", "to": "18:00"}
    "message_contains",  # cfg: {"keywords": ["prezzo", "costo"]}
)
ACTION_TYPES = (
    # Unified send carrying the full ResolvedFlowStep surface — used by the system
    # lifecycle flows (resolved by the schedulers) and available to custom flows.
    # cfg: {"window_policy": "auto|require_template|freeform_only",
    #       "free_text": str|None, "template_id": uuid|None, "variable_mapping": {...}}
    "send",
    "send_template",  # cfg: {"template_id": uuid, "variable_mapping": {...}}
    "send_message",  # cfg: {"text": "..."} — free-text, only inside 24h window
    "wait",  # cfg: {"minutes": int}
)

# The 4 lifecycle flows that were the legacy linear `flows` (db/models/flow.py).
# When `automation_flows.system_key` is one of these, the flow is a *system*
# automation: scheduler-driven (not event-driven), trigger locked, non-deletable.
SYSTEM_AUTOMATION_KEYS = ("no_answer", "reactivation", "booking_reminder", "first_contact")


class AutomationFlow(Base, TimestampMixin):
    __tablename__ = "automation_flows"

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # A flow only fires once a merchant explicitly enables it (no half-built runs).
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # NULL for custom (event-driven) flows. One of SYSTEM_AUTOMATION_KEYS for the
    # 4 lifecycle flows: scheduler-driven, trigger locked, non-deletable, and
    # excluded from the event dispatcher. Unique per merchant (partial index).
    system_key: Mapped[str | None] = mapped_column(String(32), index=True)
    # Denormalised from the single trigger node so the dispatcher can resolve
    # subscribers for an event type without scanning nodes.
    trigger_type: Mapped[str | None] = mapped_column(String(64), index=True)
    trigger_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Editor viewport (zoom/pan) — purely cosmetic.
    canvas: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    nodes: Mapped[list[AutomationNode]] = relationship(
        back_populates="automation",
        cascade="all, delete-orphan",
    )
    edges: Mapped[list[AutomationEdge]] = relationship(
        back_populates="automation",
        cascade="all, delete-orphan",
    )


class AutomationNode(Base, TimestampMixin):
    __tablename__ = "automation_nodes"
    __table_args__ = (
        UniqueConstraint("automation_id", "node_key", name="uq_automation_nodes_key"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    automation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("automation_flows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised so the standard merchant RLS predicate applies directly.
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # trigger|condition|action
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    position_x: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    position_y: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    automation: Mapped[AutomationFlow] = relationship(back_populates="nodes")


class AutomationEdge(Base, TimestampMixin):
    __tablename__ = "automation_edges"

    id: Mapped[uuid.UUID] = uuid_pk()
    automation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("automation_flows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    target_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # default | true | false — the two outgoing edges of a condition node.
    branch: Mapped[str] = mapped_column(String(16), nullable=False, default="default")

    automation: Mapped[AutomationFlow] = relationship(back_populates="edges")
