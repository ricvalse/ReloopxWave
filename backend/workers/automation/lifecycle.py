"""Resolve a lifecycle "step" by walking a system automation graph.

This is the seam that merges the legacy linear flows into the graph model
WITHOUT touching the compliance gate: the 3 schedulers call this instead of
`FlowRepository.resolve_step`, get back the SAME `ResolvedFlowStep`, and feed it
to `decide_outbound` exactly as before.

Lives in the worker layer (not the db repo) so it can compose the pure graph walk
(`ai_core`) with persistence (`db`) without a db→ai_core layering inversion.
"""

from __future__ import annotations

import contextlib
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_core.automations import SendPlan, resolve_send_node_at, resolve_send_plan
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

    An automation fires ONLY from an enabled lavagnetta flow (ADR 0014), so every
    "not configured / not enabled" case funnels into a `decide_outbound` SKIP.

    Returns:
      * None — no system flow configured for this merchant, OR the resolved path
        has fewer sends than `attempt_index`. `decide_outbound` treats None as
        SKIP (reason "no_flow"); the scheduler never sends a built-in default.
      * ResolvedFlowStep(flow_enabled=False, …) — flow exists but is DISABLED →
        `decide_outbound` SKIPs (reason "flow_disabled").
      * ResolvedFlowStep(flow_enabled=True, …) — the merchant enabled this flow on
        the canvas → the resolved copy/template is sent. A send node left blank
        (no free_text, no approved template) SKIPs — there is NO hardcoded fallback.
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


async def resolve_lifecycle_plan(
    session: AsyncSession,
    *,
    merchant_id: UUID,
    system_key: str,
    context: dict[str, Any],
) -> SendPlan | None:
    """Return the timing PLAN of a system flow so schedulers can source cadence /
    thresholds / max-attempts from the canvas instead of the ConfigKeys (ADR 0011).

    Returns None when the system flow is absent or DISABLED — the caller then
    keeps its existing ConfigKey/default behaviour (backwards-compatible). When a
    plan is returned, its per-send `delay_minutes` is the graph's timing; the
    trigger's initial-threshold config (`no_answer.delay_minutes` /
    `lead_dormant.days`) is folded into the first send's delay when no leading
    `wait` node already set it, so the scheduler reads one uniform value.

    The caller still applies its own precedence: use the graph value when > 0,
    otherwise fall back to config.
    """
    repo = AutomationRepository(session)
    flow = await repo.get_by_system_key(merchant_id, system_key)
    if flow is None or not flow.enabled:
        return None

    nodes = [
        {"node_key": n.node_key, "kind": n.kind, "type": n.type, "config": n.config or {}}
        for n in flow.nodes
    ]
    edges = [
        {"source_key": e.source_key, "target_key": e.target_key, "branch": e.branch}
        for e in flow.edges
    ]
    plan = resolve_send_plan(nodes, edges, context=context)

    if plan.sends and plan.sends[0].delay_minutes == 0:
        trig = next((n for n in flow.nodes if n.kind == "trigger"), None)
        trig_cfg = dict((trig.config if trig else None) or {})
        with contextlib.suppress(TypeError, ValueError):
            if system_key == "no_answer" and trig_cfg.get("delay_minutes"):
                plan.sends[0].delay_minutes = int(trig_cfg["delay_minutes"])
            elif system_key == "reactivation" and trig_cfg.get("days"):
                plan.sends[0].delay_minutes = int(trig_cfg["days"]) * 1440
    return plan
