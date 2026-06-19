"""Automation flow execution engine.

Two ARQ handlers:

  * ``automation_dispatch`` — a cron that *tails* ``analytics_events`` (Redis
    cursor) and, for each event whose type maps to a trigger, enqueues an
    ``automation_run`` for every enabled automation subscribed to that trigger.
    Deliberately decoupled from the hot conversation path — it adds no code to
    ``conversation_service`` / the schedulers, it just reads the events they
    already emit.

  * ``automation_run`` — walks one automation's graph for one event, evaluating
    condition nodes and executing action nodes. ``wait`` nodes break the run and
    re-enqueue a deferred continuation, so a flow can pause for N minutes.

All WhatsApp sends respect the 24h window: free-text ``send_message`` only
inside it, ``send_template`` (approved template) anywhere — mirroring
``workers.outbound``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_core.automations import evaluate_condition, outgoing_targets
from db import (
    AutomationRepository,
    IntegrationRepository,
    ResolvedFlowStep,
    TenantContext,
    WhatsAppTemplateRepository,
    session_scope,
    tenant_session,
)
from db.models import AnalyticsEvent, Conversation, Lead, Message
from db.models.automation import AutomationFlow
from integrations.whatsapp.factory import build_whatsapp_sender
from integrations.whatsapp.templates import build_send_components, resolve_body_params
from shared import get_logger
from workers.outbound import MODE_SKIP, decide_outbound, is_within_24h, send_decision

logger = get_logger(__name__)

# analytics event_type → automation trigger_type (V1 trigger surface).
EVENT_TO_TRIGGER: dict[str, str] = {
    "message.received": "message_received",
    "booking.created": "booking_created",
    "booking.failed": "booking_failed",
    "reminder.sent": "no_answer",  # the no-answer follow-up fired = lead stayed silent
    "lead_reactivation.sent": "lead_dormant",  # the dormant scan fired for this lead
}

_CURSOR_KEY = "automation:dispatch:cursor"
_DISPATCH_LIMIT = 1000
_DEDUP_TTL = 60 * 60 * 24  # 24h — bounds duplicate runs for the same (flow, event)
_HOT_SCORE = 80
_WARM_SCORE = 40


@dataclass(slots=True)
class RunContext:
    """Everything a graph walk needs about the lead/conversation being acted on."""

    phone: str
    wa_phone_number_id: str
    within_window: bool
    score: int
    temperature: str
    name: str
    last_message: str
    lead_id: UUID | None
    conversation_id: UUID | None
    tenant_id: UUID
    merchant_id: UUID

    def as_condition_context(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "score": self.score,
            "within_24h_window": self.within_window,
            "minutes_of_day": _utc_minutes_of_day(),
            "last_message": self.last_message,
        }

    def as_template_context(self) -> dict[str, str]:
        first = (self.name or "").split(" ")[0]
        return {
            "contact.name": self.name or "",
            "contact.phone": self.phone,
            "lead.first_name": first,
            "lead.name": self.name or "",
            "lead.score": str(self.score),
        }


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #


async def automation_dispatch(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron: tail analytics_events and fan out automation_run jobs."""
    redis = ctx["redis"]
    now = datetime.now(tz=UTC)

    cursor_raw = await redis.get(_CURSOR_KEY)
    if cursor_raw is None:
        # First run ever: start from now so we don't replay historical events.
        await redis.set(_CURSOR_KEY, now.isoformat())
        return {"initialized": True}

    cursor = _parse_ts(cursor_raw) or now
    dispatched = 0
    max_ts = cursor

    async with session_scope() as session:
        events = (
            (
                await session.execute(
                    select(AnalyticsEvent)
                    .where(
                        AnalyticsEvent.occurred_at > cursor,
                        AnalyticsEvent.event_type.in_(list(EVENT_TO_TRIGGER)),
                    )
                    .order_by(AnalyticsEvent.occurred_at)
                    .limit(_DISPATCH_LIMIT)
                )
            )
            .scalars()
            .all()
        )
        repo = AutomationRepository(session)
        for ev in events:
            if ev.occurred_at and ev.occurred_at > max_ts:
                max_ts = ev.occurred_at
            if ev.merchant_id is None or ev.subject_id is None:
                continue
            trigger = EVENT_TO_TRIGGER[ev.event_type]
            for auto in await repo.list_enabled_by_trigger(
                merchant_id=ev.merchant_id, trigger_type=trigger
            ):
                await redis.enqueue_job(
                    "automation_run",
                    automation_id=str(auto.id),
                    tenant_id=str(ev.tenant_id),
                    merchant_id=str(ev.merchant_id),
                    subject_type=ev.subject_type or "",
                    subject_id=str(ev.subject_id),
                    dedup=f"{auto.id}:{ev.id}",
                )
                dispatched += 1

    await redis.set(_CURSOR_KEY, max_ts.isoformat())
    return {"events": len(events), "dispatched": dispatched}


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


async def automation_run(
    ctx: dict[str, Any],
    *,
    automation_id: str,
    tenant_id: str,
    merchant_id: str,
    subject_type: str,
    subject_id: str,
    start_keys: list[str] | None = None,
    dedup: str | None = None,
) -> dict[str, Any]:
    """Execute one automation graph for one triggering subject."""
    redis = ctx["redis"]
    settings = ctx["settings"]

    if dedup is not None and not await redis.set(
        f"auto:dedup:{dedup}", "1", nx=True, ex=_DEDUP_TTL
    ):
        return {"skipped": "duplicate"}

    if not subject_id:
        return {"skipped": "no_subject"}

    tenant_ctx = TenantContext(
        tenant_id=UUID(tenant_id),
        merchant_id=UUID(merchant_id),
        role="worker",
        actor_id=UUID(merchant_id),
    )

    deferrals: list[tuple[int, list[str]]] = []
    sent = 0
    async with tenant_session(tenant_ctx) as session:
        automation = await AutomationRepository(session).get(UUID(automation_id))
        if automation is None or not automation.enabled:
            return {"skipped": "missing_or_disabled"}
        # System lifecycle flows are scheduler-driven; never run them event-driven
        # (defense in depth — list_enabled_by_trigger already excludes them).
        if automation.system_key is not None:
            return {"skipped": "system_flow"}

        run_ctx = await _resolve_context(
            session,
            tenant_id=UUID(tenant_id),
            merchant_id=UUID(merchant_id),
            subject_type=subject_type,
            subject_id=UUID(subject_id),
        )
        if run_ctx is None:
            return {"skipped": "no_context"}

        integrations = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)
        wa = await integrations.resolve_whatsapp(run_ctx.wa_phone_number_id)
        if wa is None:
            return {"skipped": "no_channel"}

        start = start_keys if start_keys is not None else _trigger_successors(automation)
        sender = build_whatsapp_sender(
            phone_number_id=wa.phone_number_id,
            api_key=wa.api_key,
            waba_base_url=wa.waba_base_url,
        )
        try:
            sent, deferrals = await _walk(
                automation,
                run_ctx,
                start_keys=start,
                sender=sender,
                templates=WhatsAppTemplateRepository(session),
            )
        finally:
            await sender.close()

    # Schedule wait-node continuations after the session closes.
    for minutes, keys in deferrals:
        await redis.enqueue_job(
            "automation_run",
            automation_id=automation_id,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            subject_type=subject_type,
            subject_id=subject_id,
            start_keys=keys,
            _defer_by=timedelta(minutes=max(0, minutes)),
        )

    logger.info(
        "automation.run",
        automation_id=automation_id,
        merchant_id=merchant_id,
        sent=sent,
        deferred=len(deferrals),
    )
    return {"sent": sent, "deferred": len(deferrals)}


async def _walk(
    automation: AutomationFlow,
    run_ctx: RunContext,
    *,
    start_keys: list[str],
    sender: Any,
    templates: WhatsAppTemplateRepository,
) -> tuple[int, list[tuple[int, list[str]]]]:
    """Breadth-first graph walk. The graph is validated acyclic before enabling,
    so a visited-set is enough to guarantee termination."""
    nodes = {n.node_key: n for n in automation.nodes}
    edges = [
        {"source_key": e.source_key, "target_key": e.target_key, "branch": e.branch}
        for e in automation.edges
    ]
    deferrals: list[tuple[int, list[str]]] = []
    sent = 0
    visited: set[str] = set()
    queue: list[str] = list(start_keys)

    while queue:
        key = queue.pop(0)
        if key in visited:
            continue
        visited.add(key)
        node = nodes.get(key)
        if node is None:
            continue

        if node.kind == "condition":
            passed = evaluate_condition(node.type, node.config or {}, run_ctx.as_condition_context())
            queue.extend(outgoing_targets(edges, key, branch="true" if passed else "false"))
        elif node.kind == "action":
            if node.type == "wait":
                minutes = _as_int((node.config or {}).get("minutes"), 0)
                successors = outgoing_targets(edges, key)
                if successors and minutes > 0:
                    deferrals.append((minutes, successors))
                # Stop this branch here; it resumes in the deferred run.
                continue
            if await _do_action(node, run_ctx, sender=sender, templates=templates):
                sent += 1
            queue.extend(outgoing_targets(edges, key))
        else:  # trigger — only as the start anchor; follow its successors
            queue.extend(outgoing_targets(edges, key))

    return sent, deferrals


async def _do_action(
    node: Any, run_ctx: RunContext, *, sender: Any, templates: WhatsAppTemplateRepository
) -> bool:
    cfg = node.config or {}
    if node.type == "send":
        # Unified send: build a ResolvedFlowStep and reuse the same compliance
        # gate the schedulers use, so custom flows honour the 24h window too.
        template = None
        template_id = cfg.get("template_id")
        if template_id:
            try:
                template = await templates.get(UUID(str(template_id)))
            except (ValueError, TypeError):
                template = None
        step = ResolvedFlowStep(
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
        decision = decide_outbound(
            within_window=run_ctx.within_window,
            fallback_text=str(cfg.get("free_text") or "").replace("{name}", run_ctx.name or ""),
            step=step,
            context=run_ctx.as_template_context(),
        )
        if decision.mode == MODE_SKIP:
            logger.info("automation.action.skipped", node=node.node_key, reason=decision.reason)
            return False
        await send_decision(sender, to_phone=run_ctx.phone, decision=decision)
        return True

    if node.type == "send_message":
        text = str(cfg.get("text", "")).strip()
        if not text or not run_ctx.within_window:
            logger.info(
                "automation.action.skipped",
                node=node.node_key,
                reason="empty" if not text else "outside_window",
            )
            return False
        text = text.replace("{name}", run_ctx.name or "")
        await sender.send_text(to_phone=run_ctx.phone, text=text)
        return True

    if node.type == "send_template":
        template_id = cfg.get("template_id")
        if not template_id:
            return False
        tpl = await templates.get(UUID(str(template_id)))
        if tpl is None or tpl.status != "approved":
            logger.info("automation.action.skipped", node=node.node_key, reason="template_not_ready")
            return False
        body_params = resolve_body_params(
            variables=list(tpl.variables or []),
            variable_mapping=dict(cfg.get("variable_mapping") or {}),
            context=run_ctx.as_template_context(),
        )
        if tpl.variables and any(p == "" for p in body_params):
            logger.info("automation.action.skipped", node=node.node_key, reason="incomplete_mapping")
            return False
        await sender.send_template(
            to_phone=run_ctx.phone,
            template_name=tpl.name,
            language=tpl.language or "it",
            components=build_send_components(body_params=body_params),
        )
        return True

    return False


# --------------------------------------------------------------------------- #
# Context resolution
# --------------------------------------------------------------------------- #


async def _resolve_context(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    merchant_id: UUID,
    subject_type: str,
    subject_id: UUID,
) -> RunContext | None:
    conv: Conversation | None = None
    lead: Lead | None = None

    if subject_type == "conversation":
        conv = await session.get(Conversation, subject_id)
        if conv is not None and conv.lead_id is not None:
            lead = await session.get(Lead, conv.lead_id)
    elif subject_type == "lead":
        lead = await session.get(Lead, subject_id)
        conv = await _latest_conversation_for_lead(session, subject_id)

    phone = (conv.wa_contact_phone if conv else None) or (lead.phone if lead else None)
    wa_phone_number_id = conv.wa_phone_number_id if conv else None
    if not phone or not wa_phone_number_id:
        return None

    now = datetime.now(tz=UTC)
    within = is_within_24h(conv.last_inbound_at, now) if conv else False
    score = lead.score if lead else 0
    last_message = await _latest_inbound_text(session, conv.id) if conv else ""

    return RunContext(
        phone=phone,
        wa_phone_number_id=wa_phone_number_id,
        within_window=within,
        score=score,
        temperature=_temperature(score),
        name=(lead.name if lead else "") or "",
        last_message=last_message,
        lead_id=lead.id if lead else None,
        conversation_id=conv.id if conv else None,
        tenant_id=tenant_id,
        merchant_id=merchant_id,
    )


async def _latest_conversation_for_lead(session: AsyncSession, lead_id: UUID) -> Conversation | None:
    stmt = (
        select(Conversation)
        .where(Conversation.lead_id == lead_id)
        .order_by(Conversation.last_message_at.desc().nullslast())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _latest_inbound_text(session: AsyncSession, conversation_id: UUID) -> str:
    stmt = (
        select(Message.content)
        .where(Message.conversation_id == conversation_id, Message.direction == "in")
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return str((await session.execute(stmt)).scalars().first() or "")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _trigger_successors(automation: AutomationFlow) -> list[str]:
    trigger = next((n for n in automation.nodes if n.kind == "trigger"), None)
    if trigger is None:
        return []
    edges = [
        {"source_key": e.source_key, "target_key": e.target_key, "branch": e.branch}
        for e in automation.edges
    ]
    return outgoing_targets(edges, trigger.node_key)


def _temperature(score: int) -> str:
    if score >= _HOT_SCORE:
        return "hot"
    if score >= _WARM_SCORE:
        return "warm"
    return "cold"


def _utc_minutes_of_day() -> int:
    now = datetime.now(tz=UTC)
    return now.hour * 60 + now.minute


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_ts(raw: Any) -> datetime | None:
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
