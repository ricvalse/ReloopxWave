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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_core.automations import (
    _ASYNC_CONDITION_TYPES,
    _ATOMIC_CONDITION_TYPES,
    evaluate_condition,
    outgoing_targets,
    wait_minutes,
)
from ai_core.conversation_service import TurnContext, build_cascade_system_prompt
from ai_core.playbook import PlaybookRuntime, resolve_playbook_runtime
from ai_core.llm import ChatMessage
from ai_core.orchestrator import ConversationContext
from ai_core.router import RoutingRequest
from db import (
    AnalyticsRepository,
    AutomationRepository,
    ConversationProfileRepository,
    ConversationRepository,
    GHLMarketplaceRepository,
    IntegrationRepository,
    LeadRepository,
    MessageRepository,
    OutcomeRepository,
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
from integrations.whatsapp.templates import (
    build_send_components,
    render_body_preview,
    resolve_body_params,
)
from shared import get_logger
from workers.outbound import (
    MODE_SKIP,
    MODE_TEMPLATE,
    MODE_TEXT,
    OutboundDecision,
    decide_outbound,
    is_within_24h,
    send_and_persist_decision,
    send_decision,
)

logger = get_logger(__name__)

# analytics event_type → automation trigger_type (V1 trigger surface).
# ADR 0015: the no_answer / reactivation schedulers are pure edge-triggered
# emitters — they emit `lead.no_answer` / `lead.dormant` (once per episode), which
# drive normal automations here. `reminder.sent` / `lead_reactivation.sent` are
# NOT mapped: they are pure KPI records now, not trigger signals.
EVENT_TO_TRIGGER: dict[str, str] = {
    "message.received": "message_received",
    "booking.created": "booking_created",
    "booking.failed": "booking_failed",
    "lead.no_answer": "no_answer",  # the lead went silent past the configured delay
    "lead.dormant": "lead_dormant",  # the lead crossed the dormancy threshold
    # ADR 0016: emitted by the GHL marketplace webhook handler (CRM-originated).
    "lead.crm_created": "crm_lead_created",
    "opportunity.created": "crm_opportunity_created",
    # Human takeover happened (escalate_human / unsupported media / human_handoff
    # node) — lets a merchant notify an operator (e.g. Slack) off a handoff.
    "conversation.escalated": "conversation_escalated",
    # An open handoff crossed the SLA (emitted by the handoff_sla_sweep cron).
    "conversation.handoff_overdue": "conversation_handoff_overdue",
}

_CURSOR_KEY = "automation:dispatch:cursor"
_DISPATCH_LIMIT = 1000
_DEDUP_TTL = 60 * 60 * 24  # 24h — bounds duplicate runs for the same (flow, event)
# The claim a run holds while it is still executing. Long enough to cover a walk
# (including Slack's retry budget), short enough that a crashed run frees the key
# before the dispatcher's lookback window closes on the event.
_DEDUP_PENDING_TTL = 300
# `occurred_at` is the emitting transaction's START (Postgres now() default) but
# the row only becomes visible at COMMIT: a long emitter transaction (e.g. the
# GHL crm-create handler) can land *behind* a cursor another event already
# advanced, and a strict `> cursor` scan would then never see it. Re-scanning a
# lookback window heals that: duplicate dispatches are suppressed by the
# per-(flow, event) Redis dedup in `automation_run`.
_DISPATCH_LOOKBACK_S = 120
_HOT_SCORE = 80
_WARM_SCORE = 40
# V1 default pipeline-advance threshold surfaced to the AI in `ai_reply` (the
# inbound path resolves this from config; the automation engine uses the default).
_ADVANCE_SCORE = 60
# Node types that put a message in front of the customer. Gated on `ai_paused`
# (human takeover / soft-pause / open handoff) in `_do_action`, and the reason a
# run needs a resolvable WhatsApp channel at all. Keep in sync with the
# message-sending branches of `_do_action` / `ACTION_TYPES`.
_CUSTOMER_FACING_NODES = frozenset({"ai_reply", "send", "send_template", "send_message"})


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
    # The trigger_type of the automation currently walking this context — set by
    # `automation_run`. Lets action nodes tailor themselves to what fired the flow
    # (e.g. notify_slack picks the handoff-vs-overdue layout; human_handoff avoids
    # re-emitting the event that triggered it).
    trigger_type: str = ""
    # Quale automazione sta camminando questo contesto, e quale profilo è attivo
    # sulla conversazione. Prima di 0047 il contesto portava solo `trigger_type`:
    # al momento dell'invio il motore non sapeva dire *quale* flusso stesse
    # eseguendo, quindi la provenienza non era registrabile nemmeno volendo.
    # Sono l'attribuzione che finisce su ogni Message inviato dal grafo, e la
    # dimensione su cui la pagina Statistiche affetta le bolle.
    automation_id: UUID | None = None
    profile_id: UUID | None = None
    # L'ultimo messaggio in uscita del thread: da quale automazione e da quale
    # nodo. Precalcolati in `_resolve_context` così la condizione
    # `last_touch_node` resta pura (niente IO dentro la valutazione) e funziona
    # anche come clausola di un `condition_group` sul percorso sincrono.
    last_touch_node_key: str | None = None
    last_touch_automation_id: UUID | None = None
    # Last inbound (customer) message time — the ADR 0015 re-engagement signal the
    # stale-cadence guard compares against the episode anchor at each resume.
    last_inbound_at: datetime | None = None
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
            # Confronti per stringa: la config del nodo arriva dal JSON del
            # canvas, dove gli uuid sono testo.
            "profile_id": str(self.profile_id) if self.profile_id else "",
            "last_touch_node_key": self.last_touch_node_key or "",
            "last_touch_automation_id": (
                str(self.last_touch_automation_id) if self.last_touch_automation_id else ""
            ),
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
    # Playbook runtime (ADR 0018) — gates the proactive prompt/actions the same
    # way the inbound turn does, so an automation reminder respects the tenant's
    # use-case (e.g. a recruiting reminder never interviews). Defaults to today's
    # sales behavior when unset.
    playbook: PlaybookRuntime = field(default_factory=PlaybookRuntime)


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

    scan_from = cursor - timedelta(seconds=_DISPATCH_LOOKBACK_S)
    async with session_scope() as session:
        events = (
            (
                await session.execute(
                    select(AnalyticsEvent)
                    .where(
                        AnalyticsEvent.occurred_at > scan_from,
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
                if not _targeted_at(auto, ev.properties):
                    continue
                if not _trigger_config_match(auto.trigger_config, ev.properties, event=ev):
                    continue
                await redis.enqueue_job(
                    "automation_run",
                    automation_id=str(auto.id),
                    tenant_id=str(ev.tenant_id),
                    merchant_id=str(ev.merchant_id),
                    subject_type=ev.subject_type or "",
                    subject_id=str(ev.subject_id),
                    dedup=f"{auto.id}:{ev.id}",
                    # ADR 0015: carry the episode anchor so a resumed cadence can
                    # cancel itself if the lead re-engaged (see automation_run).
                    episode_anchor=(ev.properties or {}).get("episode_anchor"),
                )
                dispatched += 1

    await redis.set(_CURSOR_KEY, max_ts.isoformat())
    return {"events": len(events), "dispatched": dispatched}


def _targeted_at(auto: AutomationFlow, properties: Any) -> bool:
    """Un evento può nominare l'automazione a cui è destinato (ADR 0027).

    Gli emettitori edge-triggered valutano da sé la soglia e i filtri della
    singola automazione — devono, perché è lì che si brucia l'ancora
    dell'episodio — e poi emettono un evento **indirizzato**. Senza questo
    controllo il dispatcher lo riventaglierebbe su tutte le automazioni
    sottoscritte allo stesso trigger, che è precisamente il comportamento che
    rendeva inutile il `delay_minutes` della seconda automazione.

    Un evento senza `target_automation_id` resta broadcast, come prima.
    """
    props = properties if isinstance(properties, dict) else {}
    target = str(props.get("target_automation_id") or "").strip()
    return not target or target == str(auto.id)


def _trigger_config_match(
    trigger_config: dict[str, Any] | None,
    properties: Any,
    *,
    event: AnalyticsEvent | None = None,
) -> bool:
    """Per-trigger dispatch filter (ADR 0016): a trigger node may pin the CRM
    pipeline/stage it listens to (`pipeline_id` / `stage_id` in trigger_config);
    an event carrying a different value is not for this flow. An empty config
    value means no filter on that key.

    Da 0047 il filtro accetta anche `profile_id`, ed è il punto in cui conviene
    restringere una campagna. Un `message_received` non ha nessun filtro
    naturale: un flusso sottoscritto parte su OGNI messaggio in ingresso del
    merchant, di ogni conversazione. Filtrare qui significa non accodare proprio
    il job — mentre la stessa condizione messa nel grafo lo accoda, costruisce le
    dipendenze AI (che include una query di storico) e solo dopo lo scarta.
    """
    cfg = trigger_config or {}
    props = properties if isinstance(properties, dict) else {}
    for key in ("pipeline_id", "stage_id"):
        wanted = str(cfg.get(key) or "").strip()
        if wanted and str(props.get(key) or "") != wanted:
            return False
    wanted_profile = str(cfg.get("profile_id") or "").strip()
    if wanted_profile:
        # La colonna dell'evento è la fonte di verità; `properties` resta come
        # ripiego per gli emettitori che non sono ancora stati aggiornati.
        actual = str(getattr(event, "profile_id", None) or props.get("profile_id") or "")
        if actual != wanted_profile:
            return False
    return True


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
    episode_anchor: str | None = None,
) -> dict[str, Any]:
    """Execute one automation graph for one triggering subject."""
    redis = ctx["redis"]
    settings = ctx["settings"]

    # Claimed short first, promoted to the full window only once the run has
    # actually finished. Taking the 24h key up front meant a run that died
    # halfway — Slack unreachable, a DB blip — burned its own dedup key and the
    # notification was gone for a day, silently. With a short claim the
    # dispatcher's re-scan can pick the event up again on the next tick.
    dedup_key = f"auto:dedup:{dedup}" if dedup is not None else None
    if dedup_key is not None and not await redis.set(
        dedup_key, "1", nx=True, ex=_DEDUP_PENDING_TTL
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

        run_ctx = await _resolve_context(
            session,
            tenant_id=UUID(tenant_id),
            merchant_id=UUID(merchant_id),
            subject_type=subject_type,
            subject_id=UUID(subject_id),
        )
        if run_ctx is None:
            return {"skipped": "no_context"}

        # ADR 0015 stale-cadence guard: an edge-triggered episode (no_answer /
        # dormant) carries the anchor timestamp it fired for. If the lead has since
        # re-engaged (a newer inbound than the anchor), the episode is over — abort
        # the remaining cadence instead of pestering someone who already replied.
        if _episode_ended(episode_anchor, run_ctx.last_inbound_at):
            return {"skipped": "episode_ended"}

        integrations = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)
        # Only a flow that actually messages the customer needs a WhatsApp
        # channel. Requiring one unconditionally meant a pure-notification flow
        # (handoff → Slack) never ran for a merchant whose channel wasn't
        # resolvable — precisely the merchant most likely to be handling threads
        # by hand, and the one an operator alert exists for.
        needs_channel = any(n.type in _CUSTOMER_FACING_NODES for n in automation.nodes)
        wa = await integrations.resolve_whatsapp(run_ctx.wa_phone_number_id)
        if wa is None and needs_channel:
            return {"skipped": "no_channel"}

        run_ctx.api_key = wa.api_key if wa else ""
        run_ctx.waba_base_url = wa.waba_base_url if wa else None
        run_ctx.trigger_type = automation.trigger_type or ""
        # L'attribuzione degli invii di questo walk. `_resolve_context` non può
        # saperlo (risolve lead/conversazione, non il flusso): è qui che il
        # contesto viene legato all'automazione che lo sta percorrendo.
        run_ctx.automation_id = automation.id
        # Pigre di proposito: costruirle qui costava una query di storico e due
        # letture di config per ogni run di un flusso con nodi AI — cioè per ogni
        # messaggio in ingresso del merchant su un trigger `message_received`,
        # anche quando i cancelli deterministici spengono il ramo subito dopo.
        ai_deps = _LazyAiDeps(
            lambda: _build_ai_reply_deps(ctx, session, automation, run_ctx, UUID(merchant_id))
        )

        start = start_keys if start_keys is not None else _trigger_successors(automation)
        sender = (
            build_whatsapp_sender(
                phone_number_id=wa.phone_number_id,
                api_key=wa.api_key,
                waba_base_url=wa.waba_base_url,
            )
            if wa is not None
            else None
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
            if sender is not None:
                await sender.close()

    # Schedule wait-node continuations after the session closes. Each continuation
    # carries a deterministic dedup key derived from this run's key + the resume
    # nodes, so an ARQ retry/re-delivery of the deferred job can't double-send the
    # segment (the deferred run re-checks the key at start via the `dedup` gate).
    dedup_base = dedup if dedup is not None else f"{automation_id}:{subject_id}"
    for minutes, keys in deferrals:
        await redis.enqueue_job(
            "automation_run",
            automation_id=automation_id,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            subject_type=subject_type,
            subject_id=subject_id,
            start_keys=keys,
            dedup=f"{dedup_base}:{'-'.join(sorted(keys))}",
            episode_anchor=episode_anchor,
            _defer_by=timedelta(minutes=max(0, minutes)),
        )

    # The run made it: hold the key for the full window so a re-scan of the same
    # event (the 120s lookback, an ARQ re-delivery) can't run the graph twice.
    if dedup_key is not None:
        await redis.expire(dedup_key, _DEDUP_TTL)

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
    ai_deps: AiReplyDeps | _LazyAiDeps | None = None,
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
            cfg = node.config or {}
            if node.type == "ai_check":
                passed = await _evaluate_ai_check(cfg, run_ctx, ai_deps, label=node.node_key)
            elif node.type == "has_outcome":
                passed = await _evaluate_has_outcome(
                    cfg, run_ctx, session=session, label=node.node_key
                )
            elif node.type == "condition_group" and _group_has_async_clause(cfg):
                passed = await _evaluate_group_async(
                    cfg, run_ctx, ai_deps, session=session, label=node.node_key
                )
            else:
                passed = evaluate_condition(node.type, cfg, run_ctx.as_condition_context())
            queue.extend(outgoing_targets(edges, key, branch="true" if passed else "false"))
        elif node.kind == "action":
            if node.type == "wait":
                # Honour the node's `unit` (minutes|hours|days) — `wait_minutes`
                # normalises it, matching the scheduler path (`resolve_send_plan`).
                # A raw `.get("minutes")` here ignored the unit, so a "7 days" wait
                # deferred 7 minutes.
                minutes = wait_minutes(node.config or {})
                successors = outgoing_targets(edges, key)
                if successors and minutes > 0:
                    deferrals.append((minutes, successors))
                # Stop this branch here; it resumes in the deferred run.
                continue
            if node.type == "wait_until_before":
                # ADR 0015: sends gated on a future appointment (N hours before
                # start) are delivered by the dedicated appointment-reminder
                # scheduler, which reads the hours-before + copy from this same
                # node. The event engine stops the branch here so it doesn't also
                # fire the reminder at booking time.
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


async def _send_proactive(
    sender: Any,
    *,
    run_ctx: RunContext,
    decision: Any,
    session: AsyncSession | None,
    sender_type: str,
    node_key: str | None = None,
) -> None:
    """Send a proactive automation message, persisting the Message row when we
    have a conversation + session (so it shows in the inbox and delivery
    callbacks attach via wa_message_id). Falls back to a plain send otherwise.

    `node_key` è il tocco: distinguere il DM iniziale dal reminder a 7 giorni è
    ciò che rende leggibile un funnel per-tocco, ed è anche il cancello
    deterministico che permette di riconoscere "sta rispondendo *a quella*
    domanda" senza spendere una chiamata all'LLM.
    """
    if session is not None and run_ctx.conversation_id is not None:
        await send_and_persist_decision(
            sender,
            to_phone=run_ctx.phone,
            decision=decision,
            session=session,
            conversation_id=run_ctx.conversation_id,
            merchant_id=run_ctx.merchant_id,
            sender_type=sender_type,
            automation_id=run_ctx.automation_id,
            automation_node_key=node_key,
            profile_id=run_ctx.profile_id,
        )
    else:
        await send_decision(sender, to_phone=run_ctx.phone, decision=decision)


async def _do_action(
    node: Any,
    run_ctx: RunContext,
    *,
    sender: Any,
    templates: WhatsAppTemplateRepository,
    ai_deps: AiReplyDeps | _LazyAiDeps | None = None,
    session: AsyncSession | None = None,
    settings: Any = None,
) -> bool:
    cfg = node.config or {}
    # A human owns this thread — nothing addressed to the customer goes out over
    # their head. The gate used to live inside `_do_ai_reply` only, so a flow
    # whose node was a plain `send` (a reminder, a re-engagement nudge) still
    # messaged a customer the operator was actively handling. Internal nodes
    # (`notify_slack`, `set_lead_field`, `human_handoff`, conditions) keep
    # running: they are how the operator gets told about the thread at all.
    if node.type in _CUSTOMER_FACING_NODES and run_ctx.ai_paused:
        logger.info("automation.action.skipped", node=node.node_key, reason="takeover")
        return False
    if node.type == "ai_reply":
        return await _do_ai_reply(
            node,
            cfg,
            run_ctx,
            sender=sender,
            templates=templates,
            ai_deps=ai_deps,
            session=session,
        )
    if node.type == "set_lead_field":
        return await _do_set_lead_field(node, cfg, run_ctx, session=session, settings=settings)
    if node.type == "emit_outcome":
        return await _do_emit_outcome(node, cfg, run_ctx, session=session)
    if node.type == "set_conversation_profile":
        return await _do_set_conversation_profile(node, cfg, run_ctx, session=session)
    if node.type == "human_handoff":
        return await _do_human_handoff(node, cfg, run_ctx, session=session)
    if node.type == "notify_slack":
        return await _do_notify_slack(node, cfg, run_ctx, session=session, settings=settings)
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
            template_body=template.body if template else None,
        )
        decision = decide_outbound(
            within_window=run_ctx.within_window,
            step=step,
            context=run_ctx.as_template_context(),
        )
        if decision.mode == MODE_SKIP:
            logger.info("automation.action.skipped", node=node.node_key, reason=decision.reason)
            return False
        await _send_proactive(
            sender,
            run_ctx=run_ctx,
            decision=decision,
            session=session,
            sender_type="automation",
            node_key=node.node_key,
        )
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
        # Through _send_proactive so the Message row lands in the inbox too —
        # a raw sender.send_text left this node's sends invisible in the UI.
        await _send_proactive(
            sender,
            run_ctx=run_ctx,
            decision=OutboundDecision(mode=MODE_TEXT, text=text),
            session=session,
            sender_type="automation",
            node_key=node.node_key,
        )
        return True

    if node.type == "send_template":
        template_id = cfg.get("template_id")
        if not template_id:
            return False
        tpl = await templates.get(UUID(str(template_id)))
        if tpl is None or tpl.status != "approved":
            logger.info(
                "automation.action.skipped", node=node.node_key, reason="template_not_ready"
            )
            return False
        body_params = resolve_body_params(
            variables=list(tpl.variables or []),
            variable_mapping=dict(cfg.get("variable_mapping") or {}),
            context=run_ctx.as_template_context(),
        )
        if tpl.variables and any(p == "" for p in body_params):
            logger.info(
                "automation.action.skipped", node=node.node_key, reason="incomplete_mapping"
            )
            return False
        # Through _send_proactive so the Message row lands in the inbox too — a
        # raw sender.send_template left this node's sends invisible in the UI.
        # `text` is the rendered body (human-readable copy for the bubble).
        decision = OutboundDecision(
            mode=MODE_TEMPLATE,
            text=render_body_preview(tpl.body or "", body_params).strip() or None,
            template_name=tpl.name,
            template_language=tpl.language or "it",
            components=build_send_components(
                body_params=body_params,
                header_image_url=tpl.header_image_url if tpl.header_type == "IMAGE" else None,
            ),
        )
        await _send_proactive(
            sender,
            run_ctx=run_ctx,
            decision=decision,
            session=session,
            sender_type="automation",
            node_key=node.node_key,
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
    ai_deps: AiReplyDeps | _LazyAiDeps | None,
    session: AsyncSession | None = None,
) -> bool:
    """Generate a proactive AI message, send it (24h gate), dispatch AI actions."""
    ai_deps = await _deps_of(ai_deps)
    if ai_deps is None or run_ctx.conversation_id is None:
        logger.info("automation.ai_reply.skipped", node=node.node_key, reason="no_context")
        return False
    if run_ctx.ai_paused:
        logger.info("automation.ai_reply.skipped", node=node.node_key, reason="takeover")
        return False

    # Conservative default: no `allowed_actions` selected → the AI may reply but
    # dispatches no CRM side effects. The merchant opts in per node via the UI.
    allowed = set(cfg.get("allowed_actions") or [])
    # Playbook caps (ADR 0018) gate the proactive prompt just like the inbound
    # turn: reduced schema hint, dropped qualification context and authoritative
    # directives. The effective action set is the intersection of the node's
    # `allowed` and the playbook allowlist (computed inside run_proactive).
    pb = ai_deps.playbook
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
        allowed_actions=pb.allowed_actions,
        scoring_enabled=pb.scoring_enabled,
        directives=pb.directives,
        critical_keywords=pb.critical_keywords,
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
        template_body=template.body if template else None,
    )
    decision = decide_outbound(
        within_window=run_ctx.within_window,
        step=step,
        context=run_ctx.as_template_context(),
    )
    sent_ok = False
    if decision.mode == MODE_SKIP:
        logger.info("automation.ai_reply.skipped", node=node.node_key, reason=decision.reason)
    else:
        await _send_proactive(
            sender,
            run_ctx=run_ctx,
            decision=decision,
            session=session,
            sender_type="automation_ai",
            node_key=node.node_key,
        )
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


class _LazyAiDeps:
    """Costruisce le dipendenze AI **alla prima richiesta**, non prima del walk.

    Prima erano assemblate in cima a `automation_run` per ogni flusso contenente
    un nodo AI, e questo include una `list_history(limit=30)` più due letture di
    config. Su un trigger `message_received` significa pagarle a ogni messaggio
    in ingresso del merchant, anche quando i cancelli deterministici spengono il
    ramo prima di arrivare all'`ai_check` — cioè quasi sempre, se i cancelli
    fanno il loro lavoro. Differendole, chi non raggiunge un nodo AI non paga
    nulla.

    Memoizza: un flusso con un `ai_check` e un `ai_reply` le costruisce una volta.
    """

    __slots__ = ("_built", "_deps", "_factory")

    def __init__(self, factory: Callable[[], Awaitable[AiReplyDeps | None]]) -> None:
        self._factory = factory
        self._deps: AiReplyDeps | None = None
        self._built = False

    async def resolve(self) -> AiReplyDeps | None:
        if not self._built:
            self._deps = await self._factory()
            self._built = True
        return self._deps


async def _deps_of(source: AiReplyDeps | _LazyAiDeps | None) -> AiReplyDeps | None:
    """Normalizza il parametro `ai_deps`, che può essere già risolto (i test lo
    passano così) oppure pigro."""
    if source is None or isinstance(source, AiReplyDeps):
        return source
    return await source.resolve()


async def _build_ai_reply_deps(
    ctx: dict[str, Any],
    session: AsyncSession,
    automation: AutomationFlow,
    run_ctx: RunContext,
    merchant_id: UUID,
) -> AiReplyDeps | None:
    """Assemble AI deps only when the flow needs the LLM (an ai_reply/ai_check node,
    or a condition_group with an ai_check clause) and we have a conversation + a
    runtime with the orchestrator/dispatcher wired."""
    if run_ctx.conversation_id is None:
        return None
    if not _flow_uses_ai(automation):
        return None
    runtime = ctx.get("runtime")
    if runtime is None:
        return None
    messages = await MessageRepository(session).list_history(run_ctx.conversation_id, limit=30)
    # Il profilo attivo va applicato ANCHE qui, non solo sull'inbound: è il
    # rischio n.1 dichiarato da ADR 0022. Aggiornare solo il percorso inbound
    # farebbe sì che l'`ai_reply` di un'automazione ignori il profilo che
    # l'automazione stessa ha appena caricato — cioè proprio il caso d'uso
    # "Consulenza telefonica caricata da un'automazione".
    system_prompt = await build_cascade_system_prompt(
        session=session, merchant_id=merchant_id, profile_id=run_ctx.profile_id
    )
    playbook = await resolve_playbook_runtime(session, merchant_id, profile_id=run_ctx.profile_id)
    return AiReplyDeps(
        orchestrator=runtime.orchestrator,
        dispatcher=runtime.action_dispatcher,
        history=_history_to_chat(messages),
        system_prompt=system_prompt,
        hot_threshold=_HOT_SCORE,
        advance_threshold=_ADVANCE_SCORE,
        playbook=playbook,
    )


def _history_to_chat(messages: list[Message]) -> list[ChatMessage]:
    """Fold stored message roles into the LLM role set (agent → assistant)."""
    return [
        ChatMessage(role="assistant" if m.role == "agent" else m.role, content=m.content)
        for m in messages
    ]


def _group_has_ai_clause(cfg: dict[str, Any]) -> bool:
    """True if a condition_group config has at least one `ai_check` clause."""
    return any(
        isinstance(c, dict) and str(c.get("type", "")) == "ai_check"
        for c in (cfg.get("clauses") or [])
    )


def _group_has_async_clause(cfg: dict[str, Any]) -> bool:
    """True se il gruppo contiene una clausola che richiede IO (`ai_check` o
    `has_outcome`) e va quindi valutato dal percorso asincrono."""
    return any(
        isinstance(c, dict) and str(c.get("type", "")) in _ASYNC_CONDITION_TYPES
        for c in (cfg.get("clauses") or [])
    )


def _flow_uses_ai(automation: AutomationFlow) -> bool:
    """True if any node needs the LLM: an ai_reply/ai_check node, or a
    condition_group whose clauses include an ai_check."""
    for n in automation.nodes:
        if n.type in ("ai_reply", "ai_check"):
            return True
        if n.type == "condition_group" and _group_has_ai_clause(n.config or {}):
            return True
    return False


async def _evaluate_has_outcome(
    cfg: dict[str, Any],
    run_ctx: RunContext,
    *,
    session: AsyncSession | None,
    label: str,
) -> bool:
    """Il lead ha già raggiunto l'esito indicato?

    È il cancello più efficace davanti a un `ai_check`: usato negato, una
    conversazione che ha già confermato esce definitivamente dal perimetro e non
    costa più nessuna chiamata al modello.

    Fallisce **chiusa** (False) come `ai_check`: senza sessione o senza lead non
    possiamo affermare che l'esito ci sia. Attenzione che con il ramo negato
    questo significa "prosegui" — che è il comportamento giusto, perché
    l'idempotenza vera sta comunque nell'indice unique a valle.
    """
    if session is None or run_ctx.lead_id is None:
        logger.info("automation.has_outcome.skipped", node=label, reason="no_context")
        return False
    raw = str(cfg.get("outcome_id") or "").strip()
    if not raw:
        return False
    try:
        outcome_id = UUID(raw)
    except ValueError:
        logger.warning("automation.has_outcome.bad_id", node=label, outcome_id=raw)
        return False
    scope_conversation = str(cfg.get("scope") or "lead") == "conversation"
    return await OutcomeRepository(session).has_outcome(
        lead_id=run_ctx.lead_id,
        outcome_id=outcome_id,
        conversation_id=run_ctx.conversation_id if scope_conversation else None,
    )


async def _evaluate_group_async(
    cfg: dict[str, Any],
    run_ctx: RunContext,
    ai_deps: AiReplyDeps | _LazyAiDeps | None,
    *,
    session: AsyncSession | None = None,
    label: str,
) -> bool:
    """Async variant of ai_core `_evaluate_group` for a condition_group whose clauses
    may include `ai_check` (each AI clause = one LLM yes/no call). Atomic clauses
    reuse the sync `evaluate_condition`. Per-clause `negate` is honoured; an empty
    group or an unknown clause type fails closed. AND/OR from `operator`.

    Con l'operatore `and` le clausole vengono valutate in ordine e si esce alla
    prima falsa (short-circuit): mettere i cancelli deterministici prima
    dell'`ai_check` nella lista è quindi ciò che evita la chiamata al modello,
    non solo un'ottimizzazione estetica.
    """
    clauses = cfg.get("clauses") or []
    if not clauses:
        return False
    operator = str(cfg.get("operator", "and")).lower()
    context = run_ctx.as_condition_context()
    results: list[bool] = []
    for i, clause in enumerate(clauses):
        if not isinstance(clause, dict):
            results.append(False)
            continue
        ctype = str(clause.get("type", ""))
        if ctype == "ai_check":
            value = await _evaluate_ai_check(clause, run_ctx, ai_deps, label=f"{label}[{i}]")
        elif ctype == "has_outcome":
            value = await _evaluate_has_outcome(
                clause, run_ctx, session=session, label=f"{label}[{i}]"
            )
        elif ctype in _ATOMIC_CONDITION_TYPES:
            value = evaluate_condition(ctype, clause, context)
        else:
            value = False
        results.append(not value if clause.get("negate") else value)
        # Short-circuit: niente senso valutare (e pagare) le clausole successive
        # quando l'esito del gruppo è già determinato.
        if operator == "and" and not results[-1]:
            return False
        if operator == "or" and results[-1]:
            return True
    return any(results) if operator == "or" else all(results)


async def _evaluate_ai_check(
    cfg: dict[str, Any],
    run_ctx: RunContext,
    ai_deps: AiReplyDeps | _LazyAiDeps | None,
    *,
    label: str,
) -> bool:
    """Evaluate an ai_check condition (standalone node or group clause) via a
    lightweight LLM yes/no call.

    Builds a minimal context (lead info + last 10 messages) and asks the LLM
    to return {"result": true|false}. Fails closed (False) on any error.
    """
    prompt = str(cfg.get("prompt", "")).strip()
    if not prompt or run_ctx.conversation_id is None:
        return False
    ai_deps = await _deps_of(ai_deps)
    if ai_deps is None:
        logger.info("automation.ai_check.skipped", node=label, reason="no_deps")
        return False
    model_override = str(cfg.get("model") or "") or None

    history_text = "\n".join(
        f"{'AI' if m.role == 'assistant' else 'Lead'}: {m.content}" for m in ai_deps.history[-10:]
    )
    lead_context = (
        f"Lead: {run_ctx.name or 'sconosciuto'}, "
        f"punteggio={run_ctx.score}, temperatura={run_ctx.temperature}"
    )
    messages = [
        ChatMessage(
            role="system",
            content=(
                "Sei un valutatore booleano. Analizza il contesto della conversazione "
                'e rispondi SOLO con JSON {"result": true} oppure {"result": false}.'
            ),
        ),
        ChatMessage(
            role="user",
            content=(
                f"{lead_context}\n\nConversazione recente:\n"
                f"{history_text or '(nessun messaggio)'}"
                f"\n\nDomanda da valutare: {prompt}"
            ),
        ),
    ]

    try:
        import json

        req = RoutingRequest(
            merchant_id=run_ctx.merchant_id,
            tenant_id=run_ctx.tenant_id,
            context_tokens=len(history_text) // 4,
            turn_count=len(ai_deps.history),
            lead_score=run_ctx.score,
            hot_threshold=ai_deps.hot_threshold,
            escalate_keywords_matched=False,
            force_model=model_override,
        )
        client = await ai_deps.orchestrator._router.select(req)
        result = await client.complete(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        data = json.loads(result.content)
        passed = bool(data.get("result", False))
        logger.info("automation.ai_check", node=label, passed=passed)
        return passed
    except Exception as exc:
        logger.warning("automation.ai_check.error", node=label, error=str(exc))
        return False


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


async def _do_emit_outcome(
    node: Any,
    cfg: dict[str, Any],
    run_ctx: RunContext,
    *,
    session: AsyncSession | None,
) -> bool:
    """Registra un esito sul lead. Non invia nulla → ritorna False.

    Scrive un fatto, non incrementa un contatore: la bolla della pagina
    Statistiche è un COUNT su queste righe. L'operazione è idempotente per
    costruzione (`ON CONFLICT DO NOTHING` contro gli indici unique parziali di
    0047), il che è indispensabile qui — il motore è stateless (ADR 0015) e
    ri-esegue lo stesso ramo a ogni inbound, con la conferma del lead ancora
    dentro la finestra di storico che l'`ai_check` legge.

    Porta con sé i timbri (`profile_id`, `automation_id`, `automation_node_key`)
    così l'esito è affettabile per profilo e per campagna come tutto il resto.
    """
    if session is None or run_ctx.lead_id is None:
        logger.info("automation.emit_outcome.skipped", node=node.node_key, reason="no_context")
        return False
    raw = str(cfg.get("outcome_id") or "").strip()
    try:
        outcome_id = UUID(raw)
    except ValueError:
        logger.warning("automation.emit_outcome.bad_id", node=node.node_key, outcome_id=raw)
        return False

    outcomes = OutcomeRepository(session)
    definition = await outcomes.get_definition(outcome_id)
    if definition is None or not definition.enabled:
        # Statistica cancellata o disattivata mentre il grafo la referenziava
        # ancora: si salta invece di scrivere una riga orfana.
        logger.info(
            "automation.emit_outcome.skipped",
            node=node.node_key,
            reason="unknown_or_disabled_outcome",
            outcome_id=raw,
        )
        return False

    confidence = cfg.get("confidence")
    created = await outcomes.record(
        tenant_id=run_ctx.tenant_id,
        merchant_id=run_ctx.merchant_id,
        outcome_id=outcome_id,
        lead_id=run_ctx.lead_id,
        conversation_id=run_ctx.conversation_id,
        cardinality=definition.cardinality,
        source="automation",
        profile_id=run_ctx.profile_id,
        automation_id=run_ctx.automation_id,
        automation_node_key=node.node_key,
        confidence=float(confidence) if isinstance(confidence, int | float) else None,
        value=dict(cfg.get("value") or {}),
    )
    logger.info(
        "automation.emit_outcome",
        node=node.node_key,
        outcome=definition.key,
        created=created,
    )
    return False


async def _do_set_conversation_profile(
    node: Any,
    cfg: dict[str, Any],
    run_ctx: RunContext,
    *,
    session: AsyncSession | None,
) -> bool:
    """Carica un profilo di conversazione (ADR 0022). Non invia nulla → False.

    Aggiorna il puntatore vivo su `conversations.profile_id`; il turno successivo
    lo legge come livello 0 della cascata. Il `run_ctx` viene aggiornato in
    memoria così i nodi *successivi* di questa stessa passata (incluso un
    `ai_reply`) usano già il profilo appena caricato — altrimenti il caso
    "carica Consulenza e poi rispondi" userebbe ancora il profilo vecchio.
    """
    if session is None or run_ctx.conversation_id is None:
        logger.info(
            "automation.set_conversation_profile.skipped", node=node.node_key, reason="no_context"
        )
        return False
    raw = str(cfg.get("profile_id") or "").strip()
    try:
        profile_id = UUID(raw)
    except ValueError:
        logger.warning(
            "automation.set_conversation_profile.bad_id", node=node.node_key, profile_id=raw
        )
        return False

    profile = await ConversationProfileRepository(session).get(profile_id)
    if (
        profile is None
        or not profile.enabled
        or profile.merchant_id
        not in (
            None,
            run_ctx.merchant_id,
        )
    ):
        logger.info(
            "automation.set_conversation_profile.skipped",
            node=node.node_key,
            reason="unknown_or_foreign_profile",
            profile_id=raw,
        )
        return False

    await session.execute(
        update(Conversation)
        .where(Conversation.id == run_ctx.conversation_id)
        .values(profile_id=profile_id)
    )
    run_ctx.profile_id = profile_id
    logger.info("automation.set_conversation_profile", node=node.node_key, profile=profile.key)
    return False


async def _do_human_handoff(
    node: Any,
    cfg: dict[str, Any],
    run_ctx: RunContext,
    *,
    session: AsyncSession | None,
) -> bool:
    """Hand the conversation to a human operator, reusing the takeover the AI
    escalation uses (`claim_handoff`: auto_reply off + handoff_at, atomically).
    Flips the run's `ai_paused` so any downstream ai_reply node skips. Sends no
    message → False."""
    if session is None or run_ctx.conversation_id is None:
        logger.info("automation.human_handoff.skipped", node=node.node_key, reason="no_context")
        return False
    reason = str(cfg.get("reason") or "automation_handoff")
    # Atomic exactly-once takeover (ADR 0017): claim only if the bot still owns the
    # thread. This makes the node idempotent — a handoff node inside a flow that a
    # handoff/overdue event already triggered is a no-op instead of re-stamping
    # handoff_at (which would re-arm the SLA sweep into an infinite loop) and
    # re-emitting the event. The bot stays paused either way.
    claimed = await ConversationRepository(session).claim_handoff(
        run_ctx.conversation_id,
        reason=reason,
    )
    run_ctx.ai_paused = True
    if not claimed:
        logger.info(
            "automation.human_handoff.noop", node=node.node_key, reason="already_handed_off"
        )
        return False
    # Emit `conversation.escalated` so a notify_slack (or any) automation can react
    # to a canvas-driven handoff too — this path was previously silent, unlike the
    # AI `escalate_human` handler. Only on a winning claim, so a handoff already in
    # flight is never double-emitted and can't loop.
    await AnalyticsRepository(session).emit(
        tenant_id=run_ctx.tenant_id,
        merchant_id=run_ctx.merchant_id,
        event_type="conversation.escalated",
        subject_type="conversation",
        subject_id=run_ctx.conversation_id,
        properties={
            "lead_id": str(run_ctx.lead_id) if run_ctx.lead_id else None,
            "reason": reason,
            "summary": None,
            "conversation_id": str(run_ctx.conversation_id),
            "source": "automation_node",
        },
    )
    logger.info(
        "automation.human_handoff",
        node=node.node_key,
        conversation_id=str(run_ctx.conversation_id),
    )
    return False


async def _do_notify_slack(
    node: Any,
    cfg: dict[str, Any],
    run_ctx: RunContext,
    *,
    session: AsyncSession | None,
    settings: Any,
) -> bool:
    """Post a Slack notification for this conversation via the merchant's stored
    incoming webhook.

    All Slack logic lives in the isolated `notifications` lib; this is the ONLY
    core call site, and the import is kept local so removing Slack is a
    self-contained revert. Sends no WhatsApp → returns False (like the other
    non-message actions). Best-effort: a delivery failure is logged, never raised.
    """
    if session is None or settings is None:
        logger.info("automation.notify_slack.skipped", node=node.node_key, reason="no_context")
        return False

    # Local import: keeps the Slack dependency off the hot conversation path and
    # confined to this branch (isolation — see libs/notifications).
    from notifications import (
        KIND_HANDOFF,
        KIND_HANDOFF_OVERDUE,
        SlackNotification,
        send_slack_notification,
    )

    integrations = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)
    webhook = await integrations.resolve_secret("slack", run_ctx.merchant_id)
    if webhook is None:
        logger.info("automation.notify_slack.skipped", node=node.node_key, reason="no_slack")
        return False

    is_overdue = run_ctx.trigger_type == "conversation_handoff_overdue"
    is_handoff_trigger = is_overdue or run_ctx.trigger_type == "conversation_escalated"
    reason: str | None = None
    summary: str | None = None
    overdue_minutes: int | None = None
    if run_ctx.conversation_id is not None:
        conv = await session.get(Conversation, run_ctx.conversation_id)
        if conv is not None:
            handoff_open = conv.handoff_at is not None and conv.handoff_resolved_at is None
            # The alert travels behind a queue, a cron tick and a retry: by the
            # time it would land, an operator may already have taken the thread
            # and given it back. Telling them a resolved handoff is waiting is
            # worse than saying nothing — it trains people to ignore the channel.
            if is_handoff_trigger and not handoff_open:
                logger.info(
                    "automation.notify_slack.skipped",
                    node=node.node_key,
                    reason="handoff_resolved",
                )
                return False
            # Only describe a handoff when there is one. A `notify_slack` node
            # placed under an unrelated trigger (booking_created, say) would
            # otherwise inherit the reason/summary of some past escalation and
            # announce a handoff that isn't happening.
            if handoff_open:
                reason = conv.handoff_reason
                summary = conv.handoff_summary
            if is_overdue and conv.handoff_at is not None:
                delta = datetime.now(tz=UTC) - conv.handoff_at
                overdue_minutes = max(0, int(delta.total_seconds() / 60))

    custom = str(cfg.get("text")).strip() if cfg.get("text") else None
    notification = SlackNotification(
        kind=KIND_HANDOFF_OVERDUE if is_overdue else KIND_HANDOFF,
        lead_name=run_ctx.name,
        phone=run_ctx.phone,
        reason=reason,
        summary=summary,
        last_message=run_ctx.last_message,
        inbox_url=_inbox_url(settings, run_ctx.conversation_id),
        custom_text=custom,
        overdue_minutes=overdue_minutes,
    )

    async def _mark_broken(status: int, body: str) -> None:
        await integrations.mark_provider_broken(
            merchant_id=run_ctx.merchant_id,
            provider="slack",
            error=f"HTTP {status}: {body}",
        )
        logger.warning(
            "automation.notify_slack.webhook_dead",
            node=node.node_key,
            merchant_id=str(run_ctx.merchant_id),
            status=status,
        )

    delivered = await send_slack_notification(
        webhook.secret, notification, on_permanent_failure=_mark_broken
    )
    logger.info(
        "automation.notify_slack",
        node=node.node_key,
        merchant_id=str(run_ctx.merchant_id),
        conversation_id=str(run_ctx.conversation_id) if run_ctx.conversation_id else None,
        delivered=delivered,
    )
    return False


def _inbox_url(settings: Any, conversation_id: UUID | None) -> str | None:
    """Best-effort deep link to the conversation in the merchant portal, for the
    Slack "Apri conversazione" button. None when the portal URL isn't configured."""
    base = getattr(settings, "public_web_merchant_url", None)
    if not base or conversation_id is None:
        return None
    return f"{str(base).rstrip('/')}/conversations/{conversation_id}"


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
    touch_node, touch_automation = (
        await _latest_outbound_attribution(session, conv.id) if conv else (None, None)
    )
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
        # Profilo attivo sulla conversazione (ADR 0022). Letto qui e non
        # all'invio perché i `wait` ri-eseguono `_resolve_context`: una
        # continuazione differita vede il profilo *aggiornato*, non quello che
        # c'era quando il flusso è partito.
        profile_id=conv.profile_id if conv else None,
        last_touch_node_key=touch_node,
        last_touch_automation_id=touch_automation,
        last_inbound_at=conv.last_inbound_at if conv else None,
    )


async def _latest_outbound_attribution(
    session: AsyncSession, conversation_id: UUID
) -> tuple[str | None, UUID | None]:
    """Nodo e automazione dell'ultimo messaggio in uscita del thread.

    Alimenta la condizione `last_touch_node`, cioè il cancello "sta rispondendo
    *a quel* tocco". Una query servita da `ix_messages_conv_created`.
    """
    stmt = (
        select(Message.automation_node_key, Message.automation_id)
        .where(Message.conversation_id == conversation_id, Message.direction == "out")
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return (None, None)
    return (row[0], row[1])


async def _latest_conversation_for_lead(
    session: AsyncSession, lead_id: UUID
) -> Conversation | None:
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


def _episode_ended(episode_anchor: str | None, last_inbound_at: datetime | None) -> bool:
    """ADR 0015: True when an edge-triggered episode is stale — the lead sent a new
    inbound (``last_inbound_at``) after the anchor the trigger fired for, so a
    resumed cadence must stop. No anchor (event-driven, non-episodic triggers like
    booking/first-contact) or no inbound → never stale."""
    if not episode_anchor:
        return False
    anchor = _parse_ts(episode_anchor.replace("Z", "+00:00"))
    if anchor is None or last_inbound_at is None:
        return False
    return last_inbound_at > anchor
