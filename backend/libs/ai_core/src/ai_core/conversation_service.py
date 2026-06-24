"""End-to-end conversation turn handler — the UC-01 core.

This is the thing workers call when a WhatsApp message arrives. It owns:
  1. Resolving tenant/merchant from phone_number_id (via IntegrationRepository).
  2. Loading or creating the conversation and lead rows.
  3. Building the ConversationContext with system prompt + history.
  4. Invoking the ConversationOrchestrator.
  5. Persisting the user message and the assistant reply.
  6. Delegating side-effect actions (book_slot, move_pipeline, …) to registered handlers.

Downstream UCs (02/04/05/…) plug in by registering an ActionHandler in the
`ActionDispatcher`, not by forking this file.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from uuid import UUID

from sqlalchemy import select

from ai_core.corrections import build_correction_lines
from ai_core.delivery import compute_typing_delay_s, split_into_bubbles
from ai_core.llm import ChatMessage
from ai_core.orchestrator import (
    ConversationContext,
    ConversationOrchestrator,
    OrchestratorAction,
    OrchestratorResponse,
    ToolExecutor,
)
from ai_core.rag import Embedder, RAGEngine
from ai_core.scheduling import is_within_active_hours
from ai_core.scoring import derive_conversation_signals, score_lead
from ai_core.sentiment import SentimentAnalyzer
from config_resolver import ConfigKey, ConfigResolver
from db import (
    ABRepository,
    AnalyticsRepository,
    ConversationRepository,
    IntegrationRepository,
    LeadRepository,
    MessageRepository,
    ResolvedWhatsAppIntegration,
    StorePolicyRepository,
    TenantContext,
    tenant_session,
)
from shared import get_logger

logger = get_logger(__name__)

# How long the bot stays soft-paused after the merchant replies from their phone
# (360dialog Coexistence). Reset on every echo; auto-resumes when it elapses.
_PHONE_ECHO_PAUSE = timedelta(hours=2)


def _to_chat_history(history: list[Any]) -> list[ChatMessage]:
    """Map stored messages to LLM-payload roles.

    DB roles are broader than the LLM role set (`user|assistant|system|tool`):
    `agent` marks a business-side reply typed by a human (composer) or echoed
    from the merchant's phone. To the model those are assistant-side turns, so
    we fold `agent` into `assistant` — otherwise OpenAI rejects the request
    with a 400 (`Invalid value: 'agent'`).
    """
    return [
        ChatMessage(role="assistant" if m.role == "agent" else m.role, content=m.content)
        for m in history
    ]


# ---- Action dispatcher ----------------------------------------------------


class ActionHandler(Protocol):
    # Positional-only params: the dispatcher always calls handlers positionally
    # (`handler(action, ctx)`), and handlers name the 2nd arg `turn_ctx`, so the
    # protocol must not require a matching keyword name.
    async def __call__(self, action: OrchestratorAction, ctx: TurnContext, /) -> None: ...


class ActionDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[str, ActionHandler] = {}

    def register(self, kind: str, handler: ActionHandler) -> None:
        self._handlers[kind] = handler

    async def dispatch(self, actions: list[OrchestratorAction], ctx: TurnContext) -> None:
        for action in actions:
            handler = self._handlers.get(action.kind)
            if handler is None:
                logger.debug("action.no_handler", kind=action.kind)
                continue
            try:
                await handler(action, ctx)
            except Exception as e:
                logger.warning(
                    "action.handler_failed",
                    kind=action.kind,
                    error=str(e),
                    merchant_id=str(ctx.merchant_id),
                )


# ---- The turn context passed to action handlers --------------------------


@dataclass(slots=True)
class TurnContext:
    tenant_id: UUID
    merchant_id: UUID
    lead_id: UUID
    conversation_id: UUID
    lead_phone: str
    phone_number_id: str
    # Per-channel D360 key and base URL for outbound sends. Action handlers
    # receive these so they don't need to re-resolve the integration row.
    # `waba_base_url` is the per-channel host the router stored on the
    # integration row; None means "use the D360 default host".
    api_key: str = ""
    waba_base_url: str | None = None
    # A/B experiment variant this conversation is enrolled in (UC-09). Threaded
    # into every analytics.emit below so `ABRepository.metrics` can attribute
    # conversions (booking.created / lead_score_changed / pipeline.moved) to the
    # variant — without it the metrics filter on `variant_id IN (...)` matches
    # nothing and every experiment reports zero conversions.
    variant_id: str | None = None
    # Latest-turn sentiment + a snapshot of the contact data on file, written
    # onto the GHL contact as an internal note when the lead advances (UC-04:
    # "scrive note interne con sentiment e dati raccolti").
    lead_sentiment: str | None = None
    collected_data: dict[str, Any] | None = None


# ---- The sender protocol — workers inject a real WhatsApp client, tests inject a fake


class ReplySender(Protocol):
    async def send(
        self,
        *,
        phone_number_id: str,
        api_key: str,
        to_phone: str,
        text: str,
        waba_base_url: str | None = None,
    ) -> str: ...


# ---- The entry point workers call ----------------------------------------


@dataclass(slots=True, frozen=True)
class InboundResult:
    handled: bool
    conversation_id: UUID | None = None
    reply_text: str | None = None
    reason: str | None = None


@dataclass(slots=True, frozen=True)
class PhoneEchoResult:
    handled: bool
    conversation_id: UUID | None = None
    reason: str | None = None


@dataclass(slots=True, frozen=True)
class PersistOutcome:
    """Result of phase 1 (durable persistence + auto-reply gate). The worker
    uses `auto_reply_on` + `debounce_window_s` to decide whether to reply now,
    buffer for debounce, or stay silent."""

    handled: bool
    auto_reply_on: bool
    conversation_id: UUID | None = None
    merchant_id: UUID | None = None
    reason: str | None = None
    debounce_window_s: int = 0
    # Captured phase-1 context for the inline (no re-load) reply path. None when
    # auto-reply is off. The debounce-flush path ignores this and re-loads fresh.
    reply_context: _ReplyContext | None = None


@dataclass(slots=True)
class _ReplyContext:
    """Everything phase 2/3 needs to generate, deliver and score a reply.

    Built either inline (during `handle_inbound`, from phase-1 scalars) or by
    re-loading at debounce-flush time (`generate_and_send_reply`)."""

    resolved: ResolvedWhatsAppIntegration
    conv_id: UUID
    conv_variant_id: str | None
    lead_id: UUID
    lead_score: int
    lead_name: str | None
    lead_email: str | None
    lead_sentiment: str | None
    lead_pipeline_stage_id: str | None
    chat_history: list[ChatMessage]
    from_phone: str
    phone_number_id: str
    text: str
    latest_wa_message_id: str | None = None
    # UC-05 — True when the lead replied within 10min of the previous turn
    # (derived from the conversation's prior last_message_at at inbound time).
    responded_within_10min: bool = False


DEFAULT_SYSTEM_PROMPT = (
    "Sei un assistente conversazionale italiano per l'azienda. Rispondi in modo "
    "cortese, breve e professionale. Se la richiesta riguarda prenotazioni, proponi "
    "di prenotare. Se mancano informazioni critiche (nome, email, esigenza), "
    "chiedile in modo naturale, una alla volta. Non inventare fatti sull'azienda: "
    "se non sai qualcosa, dillo e offri di far contattare una persona."
)

# Fail-safe reply: when the LLM turn errors out hard (both the primary model and
# the fallback failed, or any unexpected exception), the customer must still get
# something rather than silence — we send this and hand the thread to a human.
# Mirrors Amalia's `handle_ai_conversation_safe`.
_LLM_FAILURE_MESSAGE = (
    "Grazie per il tuo messaggio! Lo passo subito a un nostro operatore che ti "
    "risponderà a brevissimo."
)

# Deterministic persona fragments: each structured enum value maps to a constant
# Italian instruction. Pure value→string so the prompt is snapshot-testable.
_FORMALITY_FRAGMENTS: dict[str, str] = {
    "dai-del-tu": "Rivolgiti sempre al cliente dando del tu, con tono cordiale e diretto.",
    "dai-del-lei": "Rivolgiti sempre al cliente dando del Lei, con tono cortese e rispettoso.",
}
_VERBOSITY_FRAGMENTS: dict[str, str] = {
    "conciso": "Mantieni risposte molto brevi: una o due frasi, vai dritto al punto.",
    "equilibrato": (
        "Mantieni risposte di lunghezza equilibrata: chiare e complete ma senza dilungarti."
    ),
    "dettagliato": (
        "Puoi fornire risposte più articolate e dettagliate quando serve, "
        "restando comunque leggibile su WhatsApp."
    ),
}
_EMOJI_FRAGMENTS: dict[str, str] = {
    "mai": "Non usare mai emoji.",
    "sobrio": (
        "Usa le emoji con parsimonia, al massimo una per messaggio e solo quando aggiungono calore."
    ),
    "libero": (
        "Puoi usare le emoji liberamente per rendere il tono più amichevole, senza esagerare."
    ),
}
# Sentiment adaptation: keyed on the PRIOR turn's lead.sentiment. "neutral"/None
# inject nothing (absent from the dict).
_SENTIMENT_FRAGMENTS: dict[str, str] = {
    "negative": (
        "Nota: nel messaggio precedente il cliente sembrava insoddisfatto o irritato. "
        "Apri con empatia, riconosci esplicitamente il problema, evita toni commerciali "
        "o di vendita e cerca prima di tutto di rassicurarlo."
    ),
    "positive": (
        "Nota: il cliente sembra ben disposto e soddisfatto. Mantieni l'entusiasmo, "
        "asseconda l'apertura e, se opportuno, proponi il passo successivo (es. prenotazione)."
    ),
}


async def build_cascade_system_prompt(
    *,
    session: Any,
    merchant_id: UUID,
    prior_sentiment: str | None = None,
    customer_message: str | None = None,
) -> str:
    """Build the per-merchant system prompt from the config cascade.

    This module-level function is the SINGLE source of truth for the default
    (non-A/B-variant) system prompt. Both the live WhatsApp turn (via
    `ConversationService._cascade_system_prompt`) and the UC-08 playground call
    it, so the playground previews the *exact* prompt the bot uses in production.

    Falls back to `DEFAULT_SYSTEM_PROMPT` when nothing is configured — so a
    brand-new merchant still gets a working bot, it just sounds generic.
    Structured persona knobs (register/verbosity/emoji/greeting/signature/
    do/dont/examples) map to deterministic Italian fragments; `register ==
    "auto"` falls back to the freeform legacy `bot.tone`. `prior_sentiment`
    (the previous turn's lead.sentiment) optionally injects an empathy/upsell
    hint, gated by `bot.sentiment_adaptation_enabled`. `customer_message` (the
    current inbound text) is matched against the merchant's playground
    corrections; the top matches are injected as mandatory overrides (UC-08).
    """
    resolver = ConfigResolver(session)

    async def _str(key: ConfigKey) -> str | None:
        try:
            value = await resolver.resolve(key, merchant_id=merchant_id)
        except Exception:
            return None
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    async def _list(key: ConfigKey) -> list[str]:
        try:
            value = await resolver.resolve(key, merchant_id=merchant_id)
        except Exception:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []

    async def _examples(key: ConfigKey) -> list[tuple[str, str]]:
        try:
            value = await resolver.resolve(key, merchant_id=merchant_id)
        except Exception:
            return []
        out: list[tuple[str, str]] = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    q = str(item.get("q", "")).strip()
                    a = str(item.get("a", "")).strip()
                    if q and a:
                        out.append((q, a))
        return out

    async def _bool(key: ConfigKey, *, default: bool) -> bool:
        try:
            value = await resolver.resolve(key, merchant_id=merchant_id)
        except Exception:
            return default
        return value if isinstance(value, bool) else default

    business_name = await _str(ConfigKey.BUSINESS_NAME)
    industry = await _str(ConfigKey.BUSINESS_INDUSTRY)
    description = await _str(ConfigKey.BUSINESS_DESCRIPTION)
    offer = await _str(ConfigKey.BUSINESS_OFFER)
    hours = await _str(ConfigKey.BUSINESS_HOURS)
    location = await _str(ConfigKey.BUSINESS_LOCATION)
    pricing_notes = await _str(ConfigKey.BUSINESS_PRICING_NOTES)
    website = await _str(ConfigKey.BUSINESS_WEBSITE)
    tone = await _str(ConfigKey.BOT_TONE) or "professionale-amichevole"
    language = await _str(ConfigKey.BOT_LANGUAGE) or "it"
    extras = await _str(ConfigKey.BOT_SYSTEM_PROMPT_ADDITIONS)
    first_message = await _str(ConfigKey.BOT_FIRST_MESSAGE)

    formality = await _str(ConfigKey.BOT_FORMALITY) or "auto"
    verbosity = await _str(ConfigKey.BOT_VERBOSITY) or "equilibrato"
    emoji_policy = await _str(ConfigKey.BOT_EMOJI_POLICY) or "sobrio"
    greeting = await _str(ConfigKey.BOT_GREETING_STYLE)
    signature = await _str(ConfigKey.BOT_SIGNATURE)
    do_phrases = await _list(ConfigKey.BOT_DO_PHRASES)
    dont_phrases = await _list(ConfigKey.BOT_DONT_PHRASES)
    examples = await _examples(ConfigKey.BOT_EXAMPLES)
    sentiment_adaptation = await _bool(ConfigKey.BOT_SENTIMENT_ADAPTATION_ENABLED, default=True)

    # Store policies — short, always-relevant facts injected straight into
    # the prompt (no RAG). Best-effort: a missing row / error yields no lines.
    policy_lines = await build_store_policy_lines(session, merchant_id)

    # Playground-trained corrections that match THIS turn's message (UC-08).
    # Empty when no message is given or nothing scores above the relevance floor.
    correction_lines = await build_correction_lines(session, merchant_id, customer_message)

    # `has_profile` keys off content the merchant actually provided — NOT the
    # always-defaulted enums — so a truly empty merchant keeps the generic
    # DEFAULT_SYSTEM_PROMPT (today's behavior). Any real content opts into
    # the assembled prompt (with the persona fragments applied).
    has_profile = any(
        [
            business_name,
            industry,
            description,
            offer,
            hours,
            location,
            pricing_notes,
            website,
            extras,
            first_message,
            greeting,
            signature,
            do_phrases,
            dont_phrases,
            examples,
            policy_lines,
            correction_lines,
        ]
    )
    if not has_profile:
        return DEFAULT_SYSTEM_PROMPT

    lines: list[str] = []
    if business_name and industry:
        lines.append(
            f"Sei un assistente conversazionale che rappresenta {business_name}, "
            f"un'attività del settore {industry}."
        )
    elif business_name:
        lines.append(f"Sei un assistente conversazionale che rappresenta {business_name}.")
    elif industry:
        lines.append(f"Sei un assistente conversazionale per un'attività del settore {industry}.")
    else:
        lines.append("Sei un assistente conversazionale per l'azienda.")

    if description:
        lines.append(f"L'attività si descrive così: {description}")
    if offer:
        lines.append(f"Offerta principale: {offer}")
    if pricing_notes:
        lines.append(f"Note sui prezzi: {pricing_notes}")
    if hours:
        lines.append(f"Orari: {hours}")
    if location:
        lines.append(f"Sede / area di copertura: {location}")
    if website:
        lines.append(f"Sito web: {website}")
    if policy_lines:
        lines.append("Politiche del negozio:\n" + "\n".join(f"- {p}" for p in policy_lines))

    # Tone-of-address: structured formality wins; "auto" keeps the legacy tone.
    tone_clause = _FORMALITY_FRAGMENTS.get(formality) or f"Mantieni un tono {tone}."
    style_bits = [
        f"Rispondi sempre in lingua {language}.",
        tone_clause,
        _VERBOSITY_FRAGMENTS.get(verbosity, _VERBOSITY_FRAGMENTS["equilibrato"]),
        _EMOJI_FRAGMENTS.get(emoji_policy, _EMOJI_FRAGMENTS["sobrio"]),
        "Sii breve, cortese e concreto. Se mancano informazioni critiche "
        "(nome, email, esigenza), chiedile una alla volta. Non inventare fatti "
        "sull'attività: se non sai qualcosa, dillo e offri di far contattare una persona.",
    ]
    lines.append(" ".join(style_bits))

    if greeting:
        lines.append(f"Stile di apertura: {greeting}")
    if first_message:
        lines.append(
            "Quando inizi una nuova conversazione (primo messaggio al lead), "
            f"esordisci con questo messaggio di benvenuto: «{first_message}»"
        )
    if signature:
        lines.append(f"Chiudi i messaggi con questa firma quando appropriato: {signature}")
    if do_phrases:
        lines.append("Espressioni e modi di dire da preferire: " + "; ".join(do_phrases) + ".")
    if dont_phrases:
        lines.append("Espressioni, argomenti o toni da evitare: " + "; ".join(dont_phrases) + ".")
    if examples:
        ex_lines = ["Esempi di stile (segui il tono, non copiarli alla lettera):"]
        ex_lines.extend(f"- Cliente: «{q}» → Tu: «{a}»" for q, a in examples)
        lines.append("\n".join(ex_lines))

    # Sentiment adaptation — uses the PRIOR turn's sentiment (zero added
    # latency). neutral/None inject nothing.
    if sentiment_adaptation and prior_sentiment in _SENTIMENT_FRAGMENTS:
        lines.append(_SENTIMENT_FRAGMENTS[prior_sentiment])

    if extras:
        lines.append("Istruzioni aggiuntive dal merchant:")
        lines.append(extras)

    # Corrections last — highest recency, and each block explicitly states it
    # overrides everything above (the merchant fixed a specific bad reply).
    lines.extend(correction_lines)

    return "\n\n".join(lines)


async def build_store_policy_lines(session: Any, merchant_id: UUID) -> list[str]:
    """Italian one-liners for the merchant's store policies (empty if none).

    Best-effort: any error (missing table during a partial migration, etc.)
    degrades to no policy lines rather than breaking the turn.
    """
    try:
        policy = await StorePolicyRepository(session).get_for_merchant(merchant_id)
    except Exception:
        return []
    if policy is None:
        return []

    out: list[str] = []
    labelled = [
        ("Spedizioni", policy.shipping_info),
        ("Resi e rimborsi", policy.return_policy),
        ("Pagamenti", policy.payment_methods),
        ("Cambi", policy.exchange_policy),
        ("Garanzia", policy.warranty_info),
        ("Contatti", policy.contact_info),
    ]
    for label, value in labelled:
        if value and value.strip():
            out.append(f"{label}: {value.strip()}")
    for custom in policy.custom_policies or []:
        if not isinstance(custom, dict):
            continue
        title = str(custom.get("title", "")).strip()
        body = str(custom.get("body", "")).strip()
        if title and body:
            out.append(f"{title}: {body}")
    return out


class ConversationService:
    """Stateless orchestration glue. Open a fresh instance per turn, or share
    one across turns — both work; no hidden per-turn state is kept on the instance.
    """

    def __init__(
        self,
        *,
        orchestrator: ConversationOrchestrator,
        action_dispatcher: ActionDispatcher,
        reply_sender: ReplySender,
        embedder: Embedder | None = None,
        sentiment: SentimentAnalyzer | None = None,
        tool_executor: ToolExecutor | None = None,
        kek_base64: str,
    ) -> None:
        self._orchestrator = orchestrator
        self._dispatcher = action_dispatcher
        self._sender = reply_sender
        self._embedder = embedder
        self._sentiment = sentiment
        # Read-only tool executor for the Amalia-style tool-use loop. When wired
        # (+ AGENT_TOOL_USE_ENABLED) the orchestrator can ground itself on live
        # availability/appointment data mid-turn. None = single-shot turns.
        self._tool_executor = tool_executor
        self._kek = kek_base64

    async def handle_inbound(
        self,
        *,
        phone_number_id: str,
        from_phone: str,
        text: str,
        wa_message_id: str | None,
        wa_timestamp_unix: int | None = None,
    ) -> InboundResult:
        """All-in-one entry: durably persist the inbound, then (if auto-reply is
        on) generate and deliver the reply using the captured phase-1 context.

        This is the synchronous path used by tests and by the worker when
        debounce is disabled. The worker enables debounce by calling
        `handle_inbound_persist` + `generate_and_send_reply` directly.
        """
        outcome = await self.handle_inbound_persist(
            phone_number_id=phone_number_id,
            from_phone=from_phone,
            text=text,
            wa_message_id=wa_message_id,
            wa_timestamp_unix=wa_timestamp_unix,
        )
        if not outcome.handled:
            return InboundResult(handled=False, reason=outcome.reason)
        if not outcome.auto_reply_on or outcome.reply_context is None:
            return InboundResult(
                handled=True,
                conversation_id=outcome.conversation_id,
                reply_text=None,
                reason=outcome.reason,
            )
        return await self._generate_and_deliver(outcome.reply_context)

    async def handle_inbound_persist(
        self,
        *,
        phone_number_id: str,
        from_phone: str,
        text: str,
        wa_message_id: str | None,
        campaign: str | None = None,
        force_handoff_reason: str | None = None,
        wa_timestamp_unix: int | None = None,
    ) -> PersistOutcome:
        """Phase 1: durably persist the inbound and evaluate the auto-reply gate.

        Always synchronous. The inbound row, the lead/conversation upsert, the
        24h-window touch and the `message.received` event commit here, so a
        delayed or failed reply can never lose the customer's message. Returns
        the gate result, the per-merchant debounce window, and (when auto-reply
        is on) the captured context for the inline reply path. Idempotent on
        `wa_message_id`: a redelivered webhook reuses the existing row.
        """
        # Resolve tenant/merchant from phone_number_id. Uses an unscoped session
        # because the integrations row is needed before we have a tenant context.
        resolved = await self._resolve_integration(phone_number_id)
        if resolved is None:
            logger.info("uc01.no_integration", phone_number_id=phone_number_id)
            return PersistOutcome(handled=False, auto_reply_on=False, reason="no_integration")

        worker_ctx = TenantContext(
            tenant_id=resolved.tenant_id,
            merchant_id=resolved.merchant_id,
            role="worker",
            actor_id=resolved.merchant_id,  # worker-owned operation
        )
        async with tenant_session(worker_ctx) as session:
            leads = LeadRepository(session)
            convs = ConversationRepository(session)
            msgs = MessageRepository(session)
            analytics = AnalyticsRepository(session)

            already_persisted = (
                await msgs.find_by_wa_message_id(wa_message_id) if wa_message_id else None
            )

            lead = await leads.upsert_by_phone(
                merchant_id=resolved.merchant_id, phone=from_phone, campaign=campaign
            )

            conv = await convs.get_active(
                merchant_id=resolved.merchant_id, wa_contact_phone=from_phone
            )
            if conv is None:
                # UC-09 — assign an A/B variant at conversation creation so every
                # downstream message and event carries the same variant_id.
                variant_id = await _assign_ab_variant(
                    session, merchant_id=resolved.merchant_id, lead_id=lead.id
                )
                conv = await convs.create(
                    merchant_id=resolved.merchant_id,
                    lead_id=lead.id,
                    wa_phone_number_id=phone_number_id,
                    wa_contact_phone=from_phone,
                    variant_id=variant_id,
                )

            # UC-05 — capture the previous turn's timestamp BEFORE this inbound
            # bumps it, so we can derive `responded_within_10min` (lead replied
            # quickly to the bot's last message / its own prior message).
            prior_last_message_at = conv.last_message_at

            # History for the LLM = turns BEFORE this inbound. On a retry the
            # inbound is already stored, so exclude it explicitly by wa_message_id.
            history = await msgs.list_history(conv.id, limit=30)
            history = [m for m in history if m.wa_message_id != wa_message_id]

            # UC-06 opt-out: a STOP/CANCELLA reply unsubscribes the lead — record
            # it, suppress auto-replies, and exclude them from reactivation. The
            # inbound message itself is still persisted below.
            opted_out = _is_opt_out(text)
            if opted_out and await leads.mark_opted_out(lead.id):
                await analytics.emit(
                    tenant_id=resolved.tenant_id,
                    merchant_id=resolved.merchant_id,
                    event_type="lead.opted_out",
                    subject_type="lead",
                    subject_id=lead.id,
                    variant_id=conv.variant_id,
                    properties={"conversation_id": str(conv.id)},
                )

            # Auto-reply gate: AND of merchant master + per-thread takeover, and
            # never auto-reply to a lead who just opted out.
            merchant_auto_reply = await self._resolve_bool(
                session, resolved.merchant_id, ConfigKey.BOT_AUTO_REPLY_ENABLED, default=False
            )
            # Soft-pause (ai_disabled_until in the future) silences the bot without
            # flipping auto_reply; it auto-resumes once the timestamp passes.
            soft_paused = (
                conv.ai_disabled_until is not None and conv.ai_disabled_until > datetime.now(UTC)
            )
            auto_reply_on = (
                bool(merchant_auto_reply and conv.auto_reply) and not opted_out and not soft_paused
            )

            # Inbound-staleness gate: don't answer a backlog that piled up while
            # the worker was down — a late reply lands out of context. The
            # message is still persisted; only the auto-reply is suppressed.
            stale = False
            if wa_timestamp_unix:
                staleness_min = await self._resolve_int(
                    session,
                    resolved.merchant_id,
                    ConfigKey.SCHEDULE_INBOUND_STALENESS_MIN,
                    default=0,
                )
                if staleness_min > 0:
                    age_s = datetime.now(UTC).timestamp() - float(wa_timestamp_unix)
                    if age_s > staleness_min * 60:
                        stale = True
            auto_reply_on = auto_reply_on and not stale

            # Unsupported media the bot can't act on (video/document): hand off to
            # a human instead of replying. Persist the inbound, flip the thread to
            # needs-human, and notify — no LLM turn.
            if force_handoff_reason:
                conv.auto_reply = False
                conv.handoff_at = datetime.now(UTC)
                conv.handoff_reason = force_handoff_reason
                conv.handoff_resolved_at = None
                auto_reply_on = False
                await analytics.emit(
                    tenant_id=resolved.tenant_id,
                    merchant_id=resolved.merchant_id,
                    event_type="conversation.escalated",
                    subject_type="conversation",
                    subject_id=conv.id,
                    variant_id=conv.variant_id,
                    properties={
                        "lead_id": str(lead.id),
                        "reason": force_handoff_reason,
                        "conversation_id": str(conv.id),
                    },
                )

            if already_persisted is None:
                await msgs.persist_user_message(
                    conversation_id=conv.id,
                    merchant_id=resolved.merchant_id,
                    content=text,
                    wa_message_id=wa_message_id,
                    variant_id=conv.variant_id,
                )
                # Open/refresh the 24h customer-service window on a new inbound.
                await convs.touch_last_inbound(conv.id)
                received_props: dict[str, Any] = {"role": "user", "lead_id": str(lead.id)}
                if not auto_reply_on:
                    received_props["auto_reply_skipped"] = True
                    received_props["reason"] = (
                        "opted_out"
                        if opted_out
                        else "merchant_off"
                        if not merchant_auto_reply
                        else "ai_paused"
                        if soft_paused
                        else "stale"
                        if stale
                        else "conversation_off"
                    )
                await analytics.emit(
                    tenant_id=resolved.tenant_id,
                    merchant_id=resolved.merchant_id,
                    event_type="message.received",
                    subject_type="conversation",
                    subject_id=conv.id,
                    variant_id=conv.variant_id,
                    properties=received_props,
                )
            await convs.touch_last_message(conv.id)

            # Capture scalars + the prepared history while the session is open;
            # the ORM objects detach after the commit below.
            conv_id = conv.id
            conv_variant_id = conv.variant_id
            lead_id = lead.id
            lead_score = lead.score
            lead_name = lead.name
            lead_email = lead.email
            lead_pipeline_stage_id = lead.pipeline_stage_id
            responded_within_10min = _responded_within_10min(prior_last_message_at)
            # Prior turn's sentiment — drives empathy/upsell adaptation this turn
            # (zero added latency; the current turn's sentiment is computed later
            # and updates the lead for the NEXT turn).
            lead_sentiment = lead.sentiment
            chat_history = _to_chat_history(history)

            # Per-merchant debounce window (0 = off). Resolved here so the worker
            # can decide to reply now or buffer, without another round-trip.
            debounce_window_s = await self._resolve_int(
                session, resolved.merchant_id, ConfigKey.DELIVERY_DEBOUNCE_WINDOW_S, default=0
            )

            if not auto_reply_on:
                logger.info(
                    "uc01.auto_reply_skipped",
                    conversation_id=str(conv_id),
                    merchant_id=str(resolved.merchant_id),
                    merchant_auto_reply=merchant_auto_reply,
                    conversation_auto_reply=conv.auto_reply,
                )
            # Exit of `async with` commits the inbound (and the skip-path analytics).

        reply_context = None
        if auto_reply_on:
            reply_context = _ReplyContext(
                resolved=resolved,
                conv_id=conv_id,
                conv_variant_id=conv_variant_id,
                lead_id=lead_id,
                lead_score=lead_score,
                lead_name=lead_name,
                lead_email=lead_email,
                lead_sentiment=lead_sentiment,
                lead_pipeline_stage_id=lead_pipeline_stage_id,
                responded_within_10min=responded_within_10min,
                chat_history=chat_history,
                from_phone=from_phone,
                phone_number_id=phone_number_id,
                text=text,
                latest_wa_message_id=wa_message_id,
            )

        return PersistOutcome(
            handled=True,
            auto_reply_on=auto_reply_on,
            conversation_id=conv_id,
            merchant_id=resolved.merchant_id,
            reason=None if auto_reply_on else "stale" if stale else "auto_reply_off",
            debounce_window_s=debounce_window_s,
            reply_context=reply_context,
        )

    async def generate_and_send_reply(
        self,
        *,
        phone_number_id: str,
        from_phone: str,
        text: str,
        wa_message_id: str | None,
        exclude_wa_message_ids: list[str] | None = None,
    ) -> InboundResult:
        """Phase 2/3 for the worker: re-resolve fresh context for `from_phone`
        and generate + deliver a reply to `text` (which may be several coalesced
        inbound messages joined by the debounce flush). Used by the debounce
        flush and the inline no-debounce worker path. `exclude_wa_message_ids`
        are dropped from the LLM history so the just-received inbound(s) aren't
        fed twice (once as history, once as the current turn).
        """
        resolved = await self._resolve_integration(phone_number_id)
        if resolved is None:
            return InboundResult(handled=False, reason="no_integration")

        exclude = set(exclude_wa_message_ids or [])
        worker_ctx = TenantContext(
            tenant_id=resolved.tenant_id,
            merchant_id=resolved.merchant_id,
            role="worker",
            actor_id=resolved.merchant_id,
        )
        async with tenant_session(worker_ctx) as session:
            leads = LeadRepository(session)
            convs = ConversationRepository(session)
            msgs = MessageRepository(session)

            conv = await convs.get_active(
                merchant_id=resolved.merchant_id, wa_contact_phone=from_phone
            )
            if conv is None:
                return InboundResult(handled=False, reason="no_conversation")
            lead = await leads.upsert_by_phone(merchant_id=resolved.merchant_id, phone=from_phone)

            history = await msgs.list_history(conv.id, limit=30)
            history = [m for m in history if m.wa_message_id not in exclude]

            rc = _ReplyContext(
                resolved=resolved,
                conv_id=conv.id,
                conv_variant_id=conv.variant_id,
                lead_id=lead.id,
                lead_score=lead.score,
                lead_name=lead.name,
                lead_email=lead.email,
                lead_sentiment=lead.sentiment,
                lead_pipeline_stage_id=lead.pipeline_stage_id,
                chat_history=_to_chat_history(history),
                from_phone=from_phone,
                phone_number_id=phone_number_id,
                text=text,
                latest_wa_message_id=wa_message_id,
            )
        return await self._generate_and_deliver(rc)

    async def _generate_and_deliver(self, rc: _ReplyContext) -> InboundResult:
        """Phase 2 (LLM + persist) and phase 3 (typing indicator, human-paced
        multi-bubble send, scoring, action dispatch). Shared by the inline and
        debounce-flush paths. Re-opens its own session; on an LLM/persist error
        only the reply rolls back — the inbound is already durable from phase 1.
        """
        resolved = rc.resolved
        worker_ctx = TenantContext(
            tenant_id=resolved.tenant_id,
            merchant_id=resolved.merchant_id,
            role="worker",
            actor_id=resolved.merchant_id,
        )

        async with tenant_session(worker_ctx) as session:
            convs = ConversationRepository(session)
            msgs = MessageRepository(session)
            analytics = AnalyticsRepository(session)

            system_prompt = await self._resolve_system_prompt(
                session=session,
                merchant_id=resolved.merchant_id,
                variant_id=rc.conv_variant_id,
                prior_sentiment=rc.lead_sentiment,
                customer_message=rc.text,
            )
            kb_chunks = []
            if self._embedder is not None:
                try:
                    top_k = await self._resolve_int(
                        session, resolved.merchant_id, ConfigKey.RAG_TOP_K, default=5
                    )
                    min_score = await self._resolve_float(
                        session, resolved.merchant_id, ConfigKey.RAG_MIN_SCORE, default=0.7
                    )
                    rag = RAGEngine(session, self._embedder)
                    kb_chunks = await rag.retrieve(
                        rc.text, merchant_id=resolved.merchant_id, top_k=top_k, min_score=min_score
                    )
                except Exception as e:
                    logger.warning("uc01.rag_failed", error=str(e))

            hot_threshold = await self._resolve_int(
                session, resolved.merchant_id, ConfigKey.SCORING_HOT_THRESHOLD, default=80
            )
            advance_threshold = await self._resolve_int(
                session, resolved.merchant_id, ConfigKey.PIPELINE_ADVANCE_THRESHOLD, default=60
            )
            # UC-04 — qualified stage id for the deterministic advancement trigger
            # below (inject move_pipeline when the score crosses the threshold,
            # rather than relying on the LLM to optionally emit it).
            qualified_stage_id = await self._resolve_optional_str(
                session, resolved.merchant_id, ConfigKey.PIPELINE_QUALIFIED_STAGE_ID
            )

            ctx = ConversationContext(
                merchant_id=resolved.merchant_id,
                tenant_id=resolved.tenant_id,
                lead_id=rc.lead_id,
                lead_score=rc.lead_score,
                hot_threshold=hot_threshold,
                system_prompt=system_prompt,
                history=rc.chat_history,
                kb_chunks=kb_chunks,
                variant_id=rc.conv_variant_id,
                advance_threshold=advance_threshold,
            )

            # UC-01 / CC-CONFIG — outside the merchant's active hours, send the
            # configured off-hours message instead of an LLM reply. Reuses the
            # normal delivery + scoring path (the synthetic response carries no
            # actions). Fails open: no/empty message or unparseable hours → reply.
            off_hours_message = await self._maybe_off_hours_message(session, resolved.merchant_id)
            response: OrchestratorResponse
            llm_failed = False
            if off_hours_message is not None:
                response = OrchestratorResponse(
                    reply_text=off_hours_message,
                    model="off_hours",
                    tokens_in=0,
                    tokens_out=0,
                    latency_ms=0,
                )
            else:
                try:
                    response = await self._run_orchestrator(session, ctx, rc)
                except Exception as e:
                    # Fail-safe: never leave the customer in silence on an LLM
                    # error. Send a courtesy line and hand off to a human (the
                    # escalate_human action below flips the thread + notifies).
                    logger.error(
                        "uc01.llm_failed_hard",
                        error=str(e),
                        merchant_id=str(resolved.merchant_id),
                        conversation_id=str(rc.conv_id),
                    )
                    llm_failed = True
                    fallback_text = (
                        await self._resolve_optional_str(
                            session, resolved.merchant_id, ConfigKey.ESCALATION_HANDOFF_MESSAGE
                        )
                        or _LLM_FAILURE_MESSAGE
                    )
                    response = OrchestratorResponse(
                        reply_text=fallback_text,
                        actions=[
                            OrchestratorAction(
                                kind="escalate_human",
                                payload={
                                    "reason": "ai_error",
                                    "customer_message_summary": (
                                        "Errore tecnico dell'assistente AI: la conversazione "
                                        "richiede un operatore umano."
                                    ),
                                },
                            )
                        ],
                        model="error_fallback",
                        tokens_in=0,
                        tokens_out=0,
                        latency_ms=0,
                    )

            # Handoff reply policy: when the bot escalates to a human the merchant
            # can force a fixed message (handoff_message) or hand off silently
            # (no customer-facing reply). State/scoring/dispatch still run.
            # On a hard LLM failure we never suppress — the customer must get a
            # reply even if silent-handoff is configured.
            suppress_reply = False
            if any(a.kind == "escalate_human" for a in response.actions):
                silent = await self._resolve_bool(
                    session,
                    resolved.merchant_id,
                    ConfigKey.ESCALATION_SILENT_HANDOFF,
                    default=False,
                )
                if silent and not llm_failed:
                    suppress_reply = True
                elif not llm_failed:
                    handoff_message = await self._resolve_optional_str(
                        session, resolved.merchant_id, ConfigKey.ESCALATION_HANDOFF_MESSAGE
                    )
                    if handoff_message:
                        response.reply_text = handoff_message

            # Sentiment (UC-04 input / UC-05 signal): cheap gpt-5-nano call on the
            # inbound text. Best-effort — never blocks the reply. Updates the lead
            # so the NEXT turn can adapt (this turn used the prior value).
            sentiment: str | None = None
            if self._sentiment is not None:
                sentiment = await self._sentiment.analyze(
                    merchant_id=resolved.merchant_id,
                    tenant_id=resolved.tenant_id,
                    text=rc.text,
                )
                if rc.lead_id is not None and sentiment:
                    await LeadRepository(session).update_sentiment(rc.lead_id, sentiment=sentiment)

            if not suppress_reply:
                await msgs.persist_assistant_message(
                    conversation_id=rc.conv_id,
                    merchant_id=resolved.merchant_id,
                    content=response.reply_text,
                    model=response.model,
                    tokens_in=response.tokens_in,
                    tokens_out=response.tokens_out,
                    latency_ms=response.latency_ms,
                    variant_id=rc.conv_variant_id,
                )
                await convs.touch_last_message(rc.conv_id)

                await analytics.emit(
                    tenant_id=resolved.tenant_id,
                    merchant_id=resolved.merchant_id,
                    event_type="message.replied",
                    subject_type="conversation",
                    subject_id=rc.conv_id,
                    variant_id=rc.conv_variant_id,
                    properties={
                        "role": "assistant",
                        "model": response.model,
                        "tokens_in": response.tokens_in,
                        "tokens_out": response.tokens_out,
                        "latency_ms": response.latency_ms,
                        "actions": [a.kind for a in response.actions],
                    },
                )

            # Resolve delivery knobs while the session is open; applied below.
            multi_bubble_max = await self._resolve_int(
                session, resolved.merchant_id, ConfigKey.DELIVERY_MULTI_BUBBLE_MAX, default=1
            )
            bubble_max_chars = await self._resolve_int(
                session, resolved.merchant_id, ConfigKey.DELIVERY_BUBBLE_MAX_CHARS, default=600
            )
            typing_indicator_enabled = await self._resolve_bool(
                session,
                resolved.merchant_id,
                ConfigKey.DELIVERY_TYPING_INDICATOR_ENABLED,
                default=False,
            )
            delay_base = await self._resolve_float(
                session, resolved.merchant_id, ConfigKey.DELIVERY_TYPING_DELAY_BASE_S, default=0.0
            )
            delay_per_char = await self._resolve_float(
                session,
                resolved.merchant_id,
                ConfigKey.DELIVERY_TYPING_DELAY_PER_CHAR_S,
                default=0.0,
            )
            delay_min = await self._resolve_float(
                session, resolved.merchant_id, ConfigKey.DELIVERY_TYPING_DELAY_MIN_S, default=0.0
            )
            delay_max = await self._resolve_float(
                session, resolved.merchant_id, ConfigKey.DELIVERY_TYPING_DELAY_MAX_S, default=0.0
            )
            jitter = await self._resolve_float(
                session, resolved.merchant_id, ConfigKey.DELIVERY_TYPING_JITTER_FRAC, default=0.0
            )
            # Exit of `async with` commits the reply.

        # Phase 3: typing indicator + human-paced multi-bubble delivery. The
        # assistant Message row stays single (clean history); we split only on
        # the wire. All bubbles go out within seconds — well inside the 24h
        # window already opened by the inbound.
        # Silent handoff: skip the wire entirely (no customer-facing reply).
        bubbles = (
            []
            if suppress_reply
            else (
                split_into_bubbles(
                    response.reply_text, max_bubbles=multi_bubble_max, max_chars=bubble_max_chars
                )
                or [response.reply_text]
            )
        )

        if bubbles and typing_indicator_enabled and rc.latest_wa_message_id:
            await self._maybe_send_typing(rc, rc.latest_wa_message_id)

        for i, bubble in enumerate(bubbles):
            delay = compute_typing_delay_s(
                bubble,
                base_s=delay_base,
                per_char_s=delay_per_char,
                min_s=delay_min,
                max_s=delay_max,
                jitter_frac=jitter,
                seed=f"{rc.conv_id}:{i}",
            )
            if delay > 0:
                await asyncio.sleep(delay)
            await self._sender.send(
                phone_number_id=rc.phone_number_id,
                api_key=resolved.api_key,
                to_phone=rc.from_phone,
                text=bubble,
                waba_base_url=resolved.waba_base_url,
            )

        turn_ctx = TurnContext(
            tenant_id=resolved.tenant_id,
            merchant_id=resolved.merchant_id,
            lead_id=rc.lead_id,
            conversation_id=rc.conv_id,
            lead_phone=rc.from_phone,
            phone_number_id=rc.phone_number_id,
            api_key=resolved.api_key,
            waba_base_url=resolved.waba_base_url,
            variant_id=rc.conv_variant_id,
            lead_sentiment=sentiment or rc.lead_sentiment,
            collected_data={"name": rc.lead_name, "email": rc.lead_email},
        )

        # UC-05 — always-on cumulative scoring. Derive behavioural signals from
        # accumulated state (name/email on file, engagement, sentiment, booking
        # intent) and merge with any content signals the LLM reported this turn,
        # then ensure exactly one update_score action carries the merged set.
        from ai_core.actions.scoring import derive_signals_from_llm_payload

        llm_signals: dict[str, bool] = {}
        for a in response.actions:
            if a.kind == "update_score":
                llm_signals.update(derive_signals_from_llm_payload(a.payload))
        merged_signals = derive_conversation_signals(
            has_name=bool(rc.lead_name),
            has_email=bool(rc.lead_email),
            turn_count=len(rc.chat_history) + 1,
            sentiment=sentiment,
            asked_for_booking=any(a.kind == "book_slot" for a in response.actions),
            responded_within_10min=rc.responded_within_10min,
            llm_signals=llm_signals,
        )
        actions = _with_score_action(response.actions, merged_signals)

        # UC-04 — deterministic pipeline advancement. The LLM may optionally emit
        # move_pipeline, but we don't rely on it: if the (this-turn) score crosses
        # the merchant's advance_threshold and the lead isn't already in the
        # qualified stage, inject a move_pipeline action ourselves so the
        # advancement is repeatable and not at the model's discretion.
        actions = _with_pipeline_advance_action(
            actions,
            merged_signals,
            advance_threshold=advance_threshold,
            qualified_stage_id=qualified_stage_id,
            current_stage_id=rc.lead_pipeline_stage_id,
        )

        # Action handlers run after the turn is durable and the reply is out.
        # Each handler manages its own session/transaction.
        await self._dispatcher.dispatch(actions, turn_ctx)

        return InboundResult(
            handled=True,
            conversation_id=rc.conv_id,
            reply_text=response.reply_text,
        )

    async def _run_orchestrator(
        self, session: Any, ctx: ConversationContext, rc: _ReplyContext
    ) -> OrchestratorResponse:
        """Run the orchestrator turn, enabling the Amalia-style tool-use loop
        when a tool executor is wired and the merchant has it on. Falls back to
        a single-shot turn otherwise (today's behavior)."""
        if self._tool_executor is None:
            return await self._orchestrator.run(ctx, rc.text)
        tool_use_enabled = await self._resolve_bool(
            session, ctx.merchant_id, ConfigKey.AGENT_TOOL_USE_ENABLED, default=True
        )
        if not tool_use_enabled:
            return await self._orchestrator.run(ctx, rc.text)
        max_iter = await self._resolve_int(
            session, ctx.merchant_id, ConfigKey.AGENT_MAX_TOOL_ITERATIONS, default=1
        )
        return await self._orchestrator.run(
            ctx,
            rc.text,
            tool_executor=self._tool_executor,
            max_iterations=max(1, max_iter),
        )

    async def _maybe_send_typing(self, rc: _ReplyContext, message_id: str) -> None:
        """Best-effort WhatsApp read receipt + "typing…" indicator. Never blocks
        the reply: a sender without the capability (e.g. a test fake) or an API
        error is swallowed. The indicator auto-dismisses after ~25s or on send."""
        send_typing = getattr(self._sender, "send_typing_indicator", None)
        if send_typing is None:
            return
        try:
            await send_typing(
                phone_number_id=rc.phone_number_id,
                api_key=rc.resolved.api_key,
                message_id=message_id,
                waba_base_url=rc.resolved.waba_base_url,
            )
        except Exception as e:  # pragma: no cover - best effort
            logger.warning("uc01.typing_indicator_failed", error=str(e))

    async def handle_phone_app_echo(
        self,
        *,
        phone_number_id: str,
        customer_phone: str,
        text: str,
        wa_message_id: str,
    ) -> PhoneEchoResult:
        """Persist a message the merchant typed on their phone Business App.

        Only fires for channels onboarded in 360dialog Coexistence mode; for
        classic API channels this code path is never reached. We mirror the
        inbound-side resolution (integration → tenant/lead/conversation) but
        skip the LLM orchestrator entirely — the customer has already received
        the reply on WhatsApp, this is purely a UI-mirror write.

        Idempotent on `wa_message_id`: if the row already exists we return
        without writing, so 360dialog retries are safe.
        """
        resolved = await self._resolve_integration(phone_number_id)
        if resolved is None:
            return PhoneEchoResult(handled=False, reason="no_integration")

        worker_ctx = TenantContext(
            tenant_id=resolved.tenant_id,
            merchant_id=resolved.merchant_id,
            role="worker",
            actor_id=resolved.merchant_id,
        )
        async with tenant_session(worker_ctx) as session:
            from db.models.conversation import Message as _Message  # avoid top-level cycle

            existing_id = (
                await session.execute(
                    select(_Message.conversation_id).where(_Message.wa_message_id == wa_message_id)
                )
            ).scalar_one_or_none()
            if existing_id is not None:
                return PhoneEchoResult(
                    handled=True,
                    conversation_id=existing_id,
                    reason="already_persisted",
                )

            leads = LeadRepository(session)
            convs = ConversationRepository(session)
            msgs = MessageRepository(session)

            lead = await leads.upsert_by_phone(
                merchant_id=resolved.merchant_id, phone=customer_phone
            )
            conv = await convs.get_active(
                merchant_id=resolved.merchant_id, wa_contact_phone=customer_phone
            )
            if conv is None:
                # First contact for this peer was the merchant texting them from
                # the phone — open the thread so the UI shows it. No A/B variant
                # is assigned: variants gate orchestrator behaviour, which echoes
                # bypass.
                conv = await convs.create(
                    merchant_id=resolved.merchant_id,
                    lead_id=lead.id,
                    wa_phone_number_id=phone_number_id,
                    wa_contact_phone=customer_phone,
                    variant_id=None,
                )

            await msgs.persist_phone_echo_message(
                conversation_id=conv.id,
                merchant_id=resolved.merchant_id,
                content=text,
                wa_message_id=wa_message_id,
            )
            await convs.touch_last_message(conv.id)
            # The merchant just answered from their phone: soft-pause the bot for
            # a couple of hours so it doesn't talk over the human. Reset on every
            # echo (each manual reply extends the window); auto-resumes after.
            conv.ai_disabled_until = datetime.now(UTC) + _PHONE_ECHO_PAUSE

        logger.info(
            "uc01.phone_echo.persisted",
            phone_number_id=phone_number_id,
            conversation_id=str(conv.id),
            wa_message_id=wa_message_id,
        )
        return PhoneEchoResult(handled=True, conversation_id=conv.id, reason="persisted")

    # ---- helpers ----------------------------------------------------------

    async def _resolve_integration(
        self, phone_number_id: str
    ) -> ResolvedWhatsAppIntegration | None:
        """Integration lookup runs without a tenant context (the lookup *is* what
        determines the tenant). We use the service-role session for this one query.
        """
        from db import session_scope

        async with session_scope() as session:
            repo = IntegrationRepository(session, kek_base64=self._kek)
            return await repo.resolve_whatsapp(phone_number_id)

    async def _resolve_system_prompt(
        self,
        *,
        session: Any,
        merchant_id: UUID,
        variant_id: str | None = None,
        prior_sentiment: str | None = None,
        customer_message: str | None = None,
    ) -> str:
        """Resolve the system prompt for this turn (UC-09 aware).

        Delegates to `PromptManager`: when the conversation is enrolled in an
        A/B experiment and the assigned variant has an authored `system`
        template, that template's body is used — this is what makes the two
        arms behave differently (the persona/sentiment block below is
        deliberately bypassed for variant prompts, to keep experiments clean;
        playground corrections likewise apply only to the cascade fallback).
        Otherwise the config-cascade prompt below is used as the fallback.
        """
        from ai_core.prompt_manager import PromptManager

        manager = PromptManager(session)
        return await manager.resolve_system_prompt(
            merchant_id=merchant_id,
            variant_id=variant_id,
            fallback=lambda: self._cascade_system_prompt(
                session=session,
                merchant_id=merchant_id,
                prior_sentiment=prior_sentiment,
                customer_message=customer_message,
            ),
        )

    async def _cascade_system_prompt(
        self,
        *,
        session: Any,
        merchant_id: UUID,
        prior_sentiment: str | None = None,
        customer_message: str | None = None,
    ) -> str:
        """Thin wrapper over the module-level `build_cascade_system_prompt`.

        The body lives at module scope so the UC-08 playground can reuse the
        exact same builder (parity with the live WhatsApp turn) without
        instantiating a full `ConversationService`.
        """
        return await build_cascade_system_prompt(
            session=session,
            merchant_id=merchant_id,
            prior_sentiment=prior_sentiment,
            customer_message=customer_message,
        )

    async def _store_policy_lines(self, session: Any, merchant_id: UUID) -> list[str]:
        """Thin wrapper over the module-level `build_store_policy_lines`."""
        return await build_store_policy_lines(session, merchant_id)

    async def _maybe_off_hours_message(self, session: Any, merchant_id: UUID) -> str | None:
        """Return the configured off-hours reply if the merchant is outside its
        active hours right now, else None (bot replies normally). Best-effort:
        any resolution error → None (fail open)."""
        try:
            resolver = ConfigResolver(session)
            active_hours = await resolver.resolve(
                ConfigKey.SCHEDULE_ACTIVE_HOURS, merchant_id=merchant_id
            )
            tz_name = await resolver.resolve(ConfigKey.SCHEDULE_TIMEZONE, merchant_id=merchant_id)
            if is_within_active_hours(
                str(active_hours) if active_hours is not None else None,
                str(tz_name) if tz_name is not None else None,
                datetime.now(tz=UTC),
            ):
                return None
            message = await resolver.resolve(
                ConfigKey.SCHEDULE_OFF_HOURS_MESSAGE, merchant_id=merchant_id
            )
            return message if isinstance(message, str) and message.strip() else None
        except Exception as e:
            logger.warning("uc01.active_hours_failed", error=str(e), merchant_id=str(merchant_id))
            return None

    async def _resolve_int(
        self, session: Any, merchant_id: UUID, key: ConfigKey, *, default: int
    ) -> int:
        try:
            resolver = ConfigResolver(session)
            value = await resolver.resolve(key, merchant_id=merchant_id)
        except Exception:
            return default
        if isinstance(value, int):
            return value
        return default

    async def _resolve_float(
        self, session: Any, merchant_id: UUID, key: ConfigKey, *, default: float
    ) -> float:
        try:
            resolver = ConfigResolver(session)
            value = await resolver.resolve(key, merchant_id=merchant_id)
        except Exception:
            return default
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        return default

    async def _resolve_bool(
        self, session: Any, merchant_id: UUID, key: ConfigKey, *, default: bool
    ) -> bool:
        try:
            resolver = ConfigResolver(session)
            value = await resolver.resolve(key, merchant_id=merchant_id)
        except Exception:
            return default
        if isinstance(value, bool):
            return value
        return default

    async def _resolve_optional_str(
        self, session: Any, merchant_id: UUID, key: ConfigKey
    ) -> str | None:
        try:
            resolver = ConfigResolver(session)
            value = await resolver.resolve(key, merchant_id=merchant_id)
        except Exception:
            return None
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None


def _with_score_action(
    actions: list[OrchestratorAction], signals: dict[str, bool]
) -> list[OrchestratorAction]:
    """Ensure a single update_score action carries the merged signals.

    Merges into the LLM's update_score action if it emitted one, else appends a
    fresh one. With no signals at all we leave the action list untouched (the
    handler would be a no-op anyway).
    """
    if not signals:
        return actions
    out: list[OrchestratorAction] = []
    found = False
    for a in actions:
        if a.kind == "update_score":
            found = True
            payload = dict(a.payload)
            payload["signals"] = signals
            out.append(OrchestratorAction(kind="update_score", payload=payload))
        else:
            out.append(a)
    if not found:
        out.append(OrchestratorAction(kind="update_score", payload={"signals": signals}))
    return out


def _with_pipeline_advance_action(
    actions: list[OrchestratorAction],
    signals: dict[str, bool],
    *,
    advance_threshold: int,
    qualified_stage_id: str | None,
    current_stage_id: str | None,
) -> list[OrchestratorAction]:
    """UC-04 — inject a deterministic move_pipeline when the score crosses the
    advance threshold.

    The behaviour is repeatable instead of being left to the LLM's discretion:
    when the merged this-turn signals score at/above `advance_threshold` and the
    lead is not already in the qualified stage, we ensure exactly one
    move_pipeline action is present (carrying reason 'score_threshold_crossed'
    and the target stage). If the LLM already emitted one we leave it untouched
    (it may carry richer payload such as value/currency).
    """
    if qualified_stage_id is None:
        return actions
    if current_stage_id == qualified_stage_id:
        return actions
    if score_lead(signals).score < advance_threshold:
        return actions
    if any(a.kind == "move_pipeline" for a in actions):
        return actions
    return [
        *actions,
        OrchestratorAction(
            kind="move_pipeline",
            payload={
                "stage_id": qualified_stage_id,
                "reason": "score_threshold_crossed",
            },
        ),
    ]


# Exact (normalised) messages that unsubscribe a lead (UC-06). Kept to exact
# matches so a sentence like "stop un attimo" doesn't accidentally opt-out.
_OPT_OUT_KEYWORDS = frozenset(
    {"stop", "cancella", "cancellami", "annulla", "disiscrivi", "disiscrivimi", "unsubscribe"}
)


def _is_opt_out(text: str) -> bool:
    return text.strip().lower().rstrip(".!") in _OPT_OUT_KEYWORDS


# UC-05 — a lead that replies within 10 minutes of the previous turn is engaged
# (behavioural signal `responded_within_10min`). Computed from the conversation's
# prior last_message_at (bumped by both inbound and outbound), captured before
# the current inbound moves it forward.
_RESPONDED_FAST_WINDOW = timedelta(minutes=10)


def _responded_within_10min(prior_last_message_at: datetime | None) -> bool:
    if prior_last_message_at is None:
        return False
    if prior_last_message_at.tzinfo is None:
        prior_last_message_at = prior_last_message_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - prior_last_message_at) <= _RESPONDED_FAST_WINDOW


async def _assign_ab_variant(session: Any, *, merchant_id: UUID, lead_id: UUID) -> str | None:
    """Pick the oldest running experiment (if any) and hash-assign the lead."""
    try:
        ab = ABRepository(session)
        running = await ab.list_active_for_merchant(merchant_id)
        if not running:
            return None
        return cast(
            "str | None",
            await ab.assign_variant(running[0], lead_id=lead_id, merchant_id=merchant_id),
        )
    except Exception as e:  # pragma: no cover — defensive only
        logger.warning("uc09.assignment_failed", error=str(e))
        return None
