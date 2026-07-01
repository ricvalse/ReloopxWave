"""Flow persistence — configurable outbound sequences (scope A).

A flow's steps bind a lifecycle trigger to a template + variable mapping +
window policy. The schedulers resolve a single step (`resolve_step`) and let the
outbound dispatcher decide free-text vs template based on the 24h window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Flow, FlowStep, WhatsAppTemplate

# Canonical lifecycle flow keys (V1). 'custom' is reserved for future use.
FLOW_NO_ANSWER = "no_answer"
FLOW_REACTIVATION = "reactivation"
FLOW_BOOKING_REMINDER = "booking_reminder"
FLOW_FIRST_CONTACT = "first_contact"
LIFECYCLE_FLOW_KEYS = (
    FLOW_NO_ANSWER,
    FLOW_REACTIVATION,
    FLOW_BOOKING_REMINDER,
    FLOW_FIRST_CONTACT,
)


@dataclass(slots=True, frozen=True)
class ResolvedFlowStep:
    """A flattened step + its bound template, ready for the dispatcher.

    `template_name` is None when the step has no template bound (free-text only).
    `template_approved` is True only when a bound template is APPROVED — outside
    the 24h window an unapproved template can't be sent.
    """

    flow_enabled: bool
    step_enabled: bool
    window_policy: str  # auto | require_template | freeform_only
    free_text: str | None
    variable_mapping: dict[str, str]
    template_name: str | None
    template_language: str | None
    template_variables: list[str] = field(default_factory=list)
    template_approved: bool = False
    # Public signed URL for an IMAGE-header template (None for text/none headers).
    # Re-supplied as the runtime header parameter on every send.
    template_header_image_url: str | None = None


class FlowRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_merchant(self, merchant_id: UUID) -> list[Flow]:
        stmt = (
            select(Flow)
            .where(Flow.merchant_id == merchant_id)
            .options(selectinload(Flow.steps))
            .order_by(Flow.key)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def get_by_key(self, merchant_id: UUID, key: str) -> Flow | None:
        stmt = (
            select(Flow)
            .where(Flow.merchant_id == merchant_id, Flow.key == key)
            .options(selectinload(Flow.steps))
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert_flow(
        self,
        *,
        merchant_id: UUID,
        key: str,
        name: str,
        enabled: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> Flow:
        flow = await self.get_by_key(merchant_id, key)
        if flow is None:
            flow = Flow(
                merchant_id=merchant_id,
                key=key,
                name=name,
                enabled=enabled,
                meta=meta or {},
            )
            self._session.add(flow)
        else:
            flow.name = name
            flow.enabled = enabled
            if meta is not None:
                flow.meta = meta
        await self._session.flush()
        return flow

    async def replace_steps(self, flow: Flow, *, steps: list[dict[str, Any]]) -> list[FlowStep]:
        """Replace all steps of a flow with the supplied list (ordered by index)."""
        for existing in list(flow.steps):
            await self._session.delete(existing)
        await self._session.flush()

        created: list[FlowStep] = []
        for i, spec in enumerate(steps):
            step = FlowStep(
                flow_id=flow.id,
                merchant_id=flow.merchant_id,
                step_index=spec.get("step_index", i),
                delay_minutes=int(spec.get("delay_minutes", 0)),
                template_id=spec.get("template_id"),
                variable_mapping=spec.get("variable_mapping") or {},
                window_policy=spec.get("window_policy", "auto"),
                free_text=spec.get("free_text"),
                enabled=bool(spec.get("enabled", True)),
            )
            self._session.add(step)
            created.append(step)
        await self._session.flush()
        return created

    async def resolve_step(
        self, *, merchant_id: UUID, key: str, step_index: int
    ) -> ResolvedFlowStep | None:
        """Resolve one step (+ its bound template) for the scheduler dispatcher.

        Returns None when the merchant has no flow configured for `key` — callers
        then fall back to their built-in copy / config-key behaviour.
        """
        stmt = (
            select(FlowStep, Flow.enabled, WhatsAppTemplate)
            .join(Flow, Flow.id == FlowStep.flow_id)
            .outerjoin(WhatsAppTemplate, WhatsAppTemplate.id == FlowStep.template_id)
            .where(
                Flow.merchant_id == merchant_id,
                Flow.key == key,
                FlowStep.step_index == step_index,
            )
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        step, flow_enabled, template = row
        return ResolvedFlowStep(
            flow_enabled=bool(flow_enabled),
            step_enabled=step.enabled,
            window_policy=step.window_policy,
            free_text=step.free_text,
            variable_mapping=dict(step.variable_mapping or {}),
            template_name=template.name if template else None,
            template_language=template.language if template else None,
            template_variables=list(template.variables) if template else [],
            template_approved=bool(template and template.status == "approved"),
            template_header_image_url=(
                template.header_image_url if template and template.header_type == "IMAGE" else None
            ),
        )
