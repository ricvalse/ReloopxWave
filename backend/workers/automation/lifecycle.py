"""Resolve a lifecycle "step" by walking a system automation graph.

This is the seam that merges the legacy linear flows into the graph model
WITHOUT touching the compliance gate: the 3 schedulers call this instead of
`FlowRepository.resolve_step`, get back the SAME `ResolvedFlowStep`, and feed it
to `decide_outbound` exactly as before.

Lives in the worker layer (not the db repo) so it can compose the pure graph walk
(`ai_core`) with persistence (`db`) without a db→ai_core layering inversion.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_core.automations import resolve_send_node_at
from db import AutomationRepository, ResolvedFlowStep
from db.models import WhatsAppTemplate


async def resolve_lifecycle_step(
    session: AsyncSession,
    *,
    merchant_id: UUID,
    system_key: str,
    attempt_index: int,
    context: dict[str, Any],
) -> ResolvedFlowStep | None:
    """Return the ResolvedFlowStep for the attempt_index-th send of a system flow.

    Returns:
      * None — no system flow configured (scheduler uses its built-in fallback
        copy, identical to the legacy "no flow" path), OR the resolved path has
        fewer sends than `attempt_index` (scheduler's max-attempts guard makes
        this effectively unreachable).
      * ResolvedFlowStep(flow_enabled=False, …) — flow exists but is DISABLED.
        Must NOT be None here, or `decide_outbound` would fall back to free text
        instead of skipping (mirrors FlowRepository.resolve_step semantics).
    """
    repo = AutomationRepository(session)
    flow = await repo.get_by_system_key(merchant_id, system_key)
    if flow is None:
        return None
    if not flow.enabled:
        return ResolvedFlowStep(
            flow_enabled=False,
            step_enabled=True,
            window_policy="auto",
            free_text=None,
            variable_mapping={},
            template_name=None,
            template_language=None,
        )

    nodes = [
        {"node_key": n.node_key, "kind": n.kind, "type": n.type, "config": n.config or {}}
        for n in flow.nodes
    ]
    edges = [
        {"source_key": e.source_key, "target_key": e.target_key, "branch": e.branch}
        for e in flow.edges
    ]
    cfg = resolve_send_node_at(nodes, edges, attempt_index=attempt_index, context=context)
    if cfg is None:
        return None

    template: WhatsAppTemplate | None = None
    template_id = cfg.get("template_id")
    if template_id:
        try:
            template = await session.get(WhatsAppTemplate, UUID(str(template_id)))
        except (ValueError, TypeError):
            template = None

    return ResolvedFlowStep(
        flow_enabled=True,
        step_enabled=True,
        window_policy=str(cfg.get("window_policy", "auto")),
        free_text=cfg.get("free_text"),
        variable_mapping=dict(cfg.get("variable_mapping") or {}),
        template_name=template.name if template else None,
        template_language=template.language if template else None,
        template_variables=list(template.variables) if template else [],
        template_approved=bool(template and template.status == "approved"),
    )
