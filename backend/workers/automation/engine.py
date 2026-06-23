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
from ai_core.conversation_service import TurnContext, build_cascade_system_prompt
from ai_core.llm import ChatMessage
from ai_core.orchestrator import ConversationContext
from db import (
    AutomationRepository,
    ConversationRepository,
    GHLMarketplaceRepository,
    IntegrationRepository,
    LeadRepository,
    MessageRepository,
    ResolvedFlowStep,
    TenantContext,
    WhatsAppTemplateRepository,
    session_scope,
    tenant_session,
)
from db.models import AnalyticsEvent, Conversation, Lead, Message
from db.models.automation import AutomationFlow
from integrations.ghl.client import GHLClient, GHLTokenBundle
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
# V1 default pipeline-advance threshold surfaced to the AI in `ai_reply` (the
# inbound path resolves this from config; the automation engine uses the default).
_ADVANCE_SCORE = 60


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
    # Per-channel WhatsApp creds, captured once the integration resolves; threaded
    # into the TurnContext for AI-dispatched actions (e.g. propose_slots) that send.
    api_key: str = ""
    waba_base_url: str | None = None
    # True when the bot must stay silent on this thread (human takeover / soft
    # pause): auto_reply off, an active handoff, or ai_disabled_until in the future.
    # The `ai_reply` node honours it; static sends keep their own per-type gates.
    ai_paused: bool = False

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


@dataclass(slots=True)
class AiReplyDeps:
    """Deps an `ai_reply` node needs — assembled lazily, only when the flow has
    such a node and a conversation + the runtime orchestrator are available."""

    orchestrator: Any
    dispatcher: Any
    history: list[ChatMessage]
    system_prompt: str
    hot_threshold: int
    advance_threshold: int


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

        run_ctx.api_key = wa.api_key
        run_ctx.waba_base_url = wa.waba_base_url
        ai_deps = await _build_ai_reply_deps(ctx, session, automation, run_ctx, UUID(merchant_id))

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
                ai_deps=ai_deps,
                session=session,
                settings=settings,
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
    ai_deps: AiReplyDeps | None = None,
    session: AsyncSession | None = None,
    settings: Any = None,
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
    ai_reply_fired = False  # anti-loop: at most one ai_reply per run
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
            if node.type == "ai_reply" and ai_reply_fired:
                logger.info(
                    "automation.ai_reply.skipped", node=node.node_key, reason="already_fired"
                )
                queue.extend(outgoing_targets(edges, key))
                continue
            if node.type == "ai_reply":
                ai_reply_fired = True
            if await _do_action(
                node,
                run_ctx,
                sender=sender,
                templates=templates,
                ai_deps=ai_deps,
                session=session,
                settings=settings,
            ):
                sent += 1
            queue.extend(outgoing_targets(edges, key))
        else:  # trigger — only as the start anchor; follow its successors
            queue.extend(outgoing_targets(edges, key))

    return sent, deferrals


async def _do_action(
    node: Any,
    run_ctx: RunContext,
    *,
    sender: Any,
    templates: WhatsAppTemplateRepository,
    ai_deps: AiReplyDeps | None = None,
    session: AsyncSession | None = None,
    settings: Any = None,
) -> bool:
    cfg = node.config or {}
    if node.type == "ai_reply":
        return await _do_ai_reply(
            node, cfg, run_ctx, sender=sender, templates=templates, ai_deps=ai_deps
        )
    if node.type == "set_lead_field":
        return await _do_set_lead_field(node, cfg, run_ctx, session=session, settings=settings)
    if node.type == "human_handoff":
        return await _do_human_handoff(node, cfg, run_ctx, session=session)
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


async def _do_ai_reply(
    node: Any,
    cfg: dict[str, Any],
    run_ctx: RunContext,
    *,
    sender: Any,
    templates: WhatsAppTemplateRepository,
    ai_deps: AiReplyDeps | None,
) -> bool:
    """Generate a proactive AI message, send it (24h gate), dispatch AI actions."""
    if ai_deps is None or run_ctx.conversation_id is None:
        logger.info("automation.ai_reply.skipped", node=node.node_key, reason="no_context")
        return False
    if run_ctx.ai_paused:
        logger.info("automation.ai_reply.skipped", node=node.node_key, reason="takeover")
        return False

    # Conservative default: no `allowed_actions` selected → the AI may reply but
    # dispatches no CRM side effects. The merchant opts in per node via the UI.
    allowed = set(cfg.get("allowed_actions") or [])
    conv_ctx = ConversationContext(
        merchant_id=run_ctx.merchant_id,
        tenant_id=run_ctx.tenant_id,
        lead_id=run_ctx.lead_id,
        lead_score=run_ctx.score,
        hot_threshold=ai_deps.hot_threshold,
        system_prompt=ai_deps.system_prompt,
        history=ai_deps.history,
        kb_chunks=[],
        advance_threshold=ai_deps.advance_threshold,
    )
    response = await ai_deps.orchestrator.run_proactive(
        conv_ctx,
        objective=str(cfg.get("objective", "")),
        extra_instructions=str(cfg.get("extra_instructions", "")),
        allowed_actions=allowed,
        force_model=(str(cfg["model_override"]) if cfg.get("model_override") else None),
    )

    # Send the AI text through the same 24h-window gate the `send` node uses:
    # free text inside the window, the fallback template (if approved) outside.
    template = None
    template_id = cfg.get("fallback_template_id")
    if template_id:
        try:
            template = await templates.get(UUID(str(template_id)))
        except (ValueError, TypeError):
            template = None
    step = ResolvedFlowStep(
        flow_enabled=True,
        step_enabled=True,
        window_policy=str(cfg.get("window_policy", "auto")),
        free_text=response.reply_text,
        variable_mapping={},
        template_name=template.name if template else None,
        template_language=template.language if template else None,
        template_variables=list(template.variables) if template else [],
        template_approved=bool(template and template.status == "approved"),
    )
    decision = decide_outbound(
        within_window=run_ctx.within_window,
        fallback_text=response.reply_text,
        step=step,
        context=run_ctx.as_template_context(),
    )
    sent_ok = False
    if decision.mode == MODE_SKIP:
        logger.info("automation.ai_reply.skipped", node=node.node_key, reason=decision.reason)
    else:
        await send_decision(sender, to_phone=run_ctx.phone, decision=decision)
        sent_ok = True

    # Dispatch the AI's (already-filtered) actions after the message lands —
    # mirrors the inbound turn ordering. Handlers open their own sessions.
    if response.actions and ai_deps.dispatcher is not None and run_ctx.lead_id is not None:
        turn_ctx = TurnContext(
            tenant_id=run_ctx.tenant_id,
            merchant_id=run_ctx.merchant_id,
            lead_id=run_ctx.lead_id,
            conversation_id=run_ctx.conversation_id,
            lead_phone=run_ctx.phone,
            phone_number_id=run_ctx.wa_phone_number_id,
            api_key=run_ctx.api_key,
            waba_base_url=run_ctx.waba_base_url,
        )
        await ai_deps.dispatcher.dispatch(response.actions, turn_ctx)
    return sent_ok


async def _build_ai_reply_deps(
    ctx: dict[str, Any],
    session: AsyncSession,
    automation: AutomationFlow,
    run_ctx: RunContext,
    merchant_id: UUID,
) -> AiReplyDeps | None:
    """Assemble AI-reply deps only when the flow has an ai_reply node and we have
    a conversation + a runtime with the orchestrator/dispatcher wired."""
    if run_ctx.conversation_id is None:
        return None
    if not any(n.type == "ai_reply" for n in automation.nodes):
        return None
    runtime = ctx.get("runtime")
    if runtime is None:
        return None
    messages = await MessageRepository(session).list_history(run_ctx.conversation_id, limit=30)
    system_prompt = await build_cascade_system_prompt(session=session, merchant_id=merchant_id)
    return AiReplyDeps(
        orchestrator=runtime.orchestrator,
        dispatcher=runtime.action_dispatcher,
        history=_history_to_chat(messages),
        system_prompt=system_prompt,
        hot_threshold=_HOT_SCORE,
        advance_threshold=_ADVANCE_SCORE,
    )


def _history_to_chat(messages: list[Message]) -> list[ChatMessage]:
    """Fold stored message roles into the LLM role set (agent → assistant)."""
    return [
        ChatMessage(role="assistant" if m.role == "agent" else m.role, content=m.content)
        for m in messages
    ]


async def _do_set_lead_field(
    node: Any,
    cfg: dict[str, Any],
    run_ctx: RunContext,
    *,
    session: AsyncSession | None,
    settings: Any,
) -> bool:
    """Update a lead/CRM field. Returns False (sends no WhatsApp message); success
    is observable via the info logs."""
    if session is None:
        return False
    field = str(cfg.get("field", ""))
    if field == "score_delta":
        if run_ctx.lead_id is None:
            logger.info("automation.set_lead_field.skipped", node=node.node_key, reason="no_lead")
            return False
        delta = _as_int(cfg.get("value"), 0)
        new_score = max(0, min(100, run_ctx.score + delta))
        await LeadRepository(session).update_score(
            run_ctx.lead_id,
            score=new_score,
            reasons=[f"automation:set_lead_field:{delta:+d}"],
        )
        logger.info(
            "automation.set_lead_field", node=node.node_key, field=field, new_score=new_score
        )
        return False
    if field in ("tag", "custom_field"):
        if not cfg.get("ghl_sync"):
            logger.info(
                "automation.set_lead_field.skipped", node=node.node_key, reason="ghl_sync_off"
            )
            return False
        await _set_ghl_contact_field(
            node, cfg, run_ctx, session=session, settings=settings, field=field
        )
        return False
    # `stage` (a pipeline move) is intentionally out of scope for V1 — use the
    # move_pipeline action / ai_reply for that.
    logger.info(
        "automation.set_lead_field.skipped", node=node.node_key, reason=f"unsupported:{field}"
    )
    return False


async def _set_ghl_contact_field(
    node: Any,
    cfg: dict[str, Any],
    run_ctx: RunContext,
    *,
    session: AsyncSession,
    settings: Any,
    field: str,
) -> None:
    """Write a tag / custom field onto the GHL contact via upsert_contact (the
    client has no dedicated add_tag). Best-effort: a GHL error is logged, not raised."""
    integrations = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)
    ghl = await integrations.resolve_ghl(run_ctx.merchant_id)
    if ghl is None:
        logger.info("automation.set_lead_field.skipped", node=node.node_key, reason="no_ghl")
        return

    async def _persist_tokens(bundle: GHLTokenBundle) -> None:
        # Own committed transaction so a rotated refresh token survives even if the
        # GHL call later fails (mirrors the action handlers).
        if not bundle.location_id:
            return
        async with session_scope() as token_session:
            await GHLMarketplaceRepository(
                token_session, kek_base64=settings.integrations_kek_base64
            ).set_location_token(
                location_id=bundle.location_id,
                access_token=bundle.access_token,
                refresh_token=bundle.refresh_token,
                expires_at=bundle.expires_at,
            )

    client = GHLClient(
        token_bundle=GHLTokenBundle(
            access_token=ghl.access_token,
            refresh_token=ghl.refresh_token,
            expires_at=ghl.expires_at,
            location_id=ghl.location_id,
        ),
        client_id=settings.ghl_client_id,
        client_secret=settings.ghl_client_secret,
        on_token_refresh=_persist_tokens,
    )
    payload: dict[str, Any] = {"phone": run_ctx.phone}
    if field == "tag":
        tag = str(cfg.get("value", "")).strip()
        if not tag:
            await client.close()
            return
        payload["tags"] = [tag]
    else:  # custom_field
        payload["customFields"] = {str(cfg.get("key", "")): cfg.get("value")}
    try:
        await client.upsert_contact(payload)
        logger.info("automation.set_lead_field", node=node.node_key, field=field, ghl=True)
    except Exception as e:
        logger.warning("automation.set_lead_field.failed", node=node.node_key, error=str(e))
    finally:
        await client.close()


async def _do_human_handoff(
    node: Any,
    cfg: dict[str, Any],
    run_ctx: RunContext,
    *,
    session: AsyncSession | None,
) -> bool:
    """Hand the conversation to a human operator, reusing the takeover state the AI
    escalation uses (mark_escalated: auto_reply off + handoff_at). Flips the run's
    `ai_paused` so any downstream ai_reply node skips. Sends no message → False."""
    if session is None or run_ctx.conversation_id is None:
        logger.info("automation.human_handoff.skipped", node=node.node_key, reason="no_context")
        return False
    await ConversationRepository(session).mark_escalated(
        run_ctx.conversation_id,
        reason=str(cfg.get("reason") or "automation_handoff"),
    )
    run_ctx.ai_paused = True
    logger.info(
        "automation.human_handoff",
        node=node.node_key,
        conversation_id=str(run_ctx.conversation_id),
    )
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
    # Bot silenced on this thread? (human takeover / soft-pause). Same gate the
    # inbound auto-reply path uses — an `ai_reply` node must respect it.
    ai_paused = conv is not None and (
        not conv.auto_reply
        or (conv.ai_disabled_until is not None and conv.ai_disabled_until > now)
        or (conv.handoff_at is not None and conv.handoff_resolved_at is None)
    )

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
        ai_paused=ai_paused,
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
