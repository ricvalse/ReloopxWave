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
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ai_core.corrections import build_correction_lines
from ai_core.delivery import compute_typing_delay_s, split_into_bubbles
from ai_core.llm import ChatMessage, ImagePart
from ai_core.orchestrator import (
    ConversationContext,
    ConversationOrchestrator,
    HandoffPrompt,
    OrchestratorAction,
    OrchestratorResponse,
    ToolExecutor,
)
from ai_core.playbook import resolve_playbook_runtime
from ai_core.rag import (
    KB_INLINE_MAX_TOKENS,
    Embedder,
    RAGEngine,
    kb_all_chunks,
    kb_estimated_tokens,
)
from ai_core.scheduling import is_within_active_hours
from ai_core.scoring import derive_conversation_signals, score_lead
from ai_core.sentiment import SentimentAnalyzer
from ai_core.quality.coherence import CoherenceGuard
from ai_core.quality.compressor import (
    ContextCompressor,
    MemoryBlock,
    _KEEP_RECENT,
    memory_block_as_message,
)
from ai_core.state_machine import ConvState, next_state, state_system_hint
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
from db.repositories.services import (
    BusinessClosureRepository,
    BusinessHourRepository,
    ServiceRepository,
)
from shared import get_logger

logger = get_logger(__name__)

# Ripiego se `escalation.phone_echo_pause_minutes` non risolve: quanto il bot
# resta in pausa dopo una risposta scritta a mano dal telefono del merchant
# (360dialog Coexistence). Reset a ogni eco; riparte da solo alla scadenza.
_PHONE_ECHO_PAUSE_FALLBACK_MIN = 120

# How many of the most-recent messages we pull as LLM context. Deliberately
# WIDER than the default AGENT_CONTEXT_COMPRESS_THRESHOLD (30) so the context
# compression branch is actually reachable: when a thread exceeds the threshold,
# the older turns are folded into a running memory block instead of being
# silently dropped. Keep this > the largest threshold you expect merchants to
# set, or compression for those tenants degrades back to plain truncation.
_HISTORY_FETCH_LIMIT = 80


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


# Proactive / automation sends are persisted with these `meta.sender_type`
# markers (see MessageRepository.persist_outbound_message / send_and_persist_decision).
_PROACTIVE_SENDERS = {"automation", "automation_ai"}

# Cap on the proactive message text re-injected into the system prompt — an
# approved template can be long; the intent is enough context to give continuity,
# not to duplicate the whole body.
_PROACTIVE_CONTEXT_MAX_CHARS = 600


def _trailing_proactive_text(history: list[Any]) -> str | None:
    """Text of the last turn IFF it was a proactive/automation send.

    When an automation (first-contact, no-answer reminder, reactivation, …)
    delivers a message and then the customer replies, that message IS already in
    the LLM history — but folded to a bare `assistant` turn by `_to_chat_history`,
    it gets steam-rolled by the merchant's generic persona: the bot restarts with
    a generic greeting / pivots to unrelated topics (prenotazioni, servizi)
    instead of continuing what the automation set up. This surfaced as "l'AI non
    prende in considerazione i messaggi dell'automazione".

    Returning the text here lets `_generate_and_deliver` re-inject it as an
    explicit, authoritative continuity directive. Only fires when the LAST stored
    turn (i.e. the one the customer is replying to) is proactive — once the bot
    has answered, the customer is replying to the bot, not the automation.
    """
    if not history:
        return None
    last = history[-1]
    sender = (getattr(last, "meta", None) or {}).get("sender_type")
    if sender in _PROACTIVE_SENDERS:
        text = (getattr(last, "content", "") or "").strip()
        return text or None
    return None


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
    # True when the caller already won the atomic handoff claim for this turn
    # (the inbound reply policy claims *before* sending, so the handoff message
    # goes out exactly once). `EscalateHumanHandler` then only records the
    # episode. Left False — as on the automation `ai_reply` path, which has no
    # reply policy — the handler takes the claim itself and stays silent if it
    # loses, so no path can open a second handoff on the same thread.
    handoff_claimed: bool = False


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


class MediaPipeline(Protocol):
    """Downloads inbound WhatsApp media and stores it (workers inject the real
    360dialog+Supabase impl; tests inject a fake or leave it None).

    Dependency inversion, exactly like `ReplySender`: ai_core stays unaware of
    360dialog / Supabase Storage / Whisper. `fetch_and_store` is best-effort — it
    returns a `meta.media` patch (with `storage_path`/`size_bytes`/
    `transcription`, or `error` on failure) and never raises."""

    async def fetch_and_store(
        self,
        *,
        api_key: str,
        waba_base_url: str | None,
        phone_number_id: str,
        merchant_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        media_id: str,
        kind: str,
        mime: str | None,
    ) -> dict[str, Any]: ...

    async def load_image(self, *, storage_path: str, mime: str | None) -> ImagePart | None: ...


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
    conv_current_state: str | None = None
    conv_context_summary: dict[str, Any] | None = None
    latest_wa_message_id: str | None = None
    # UC-05 — True when the lead replied within 10min of the previous turn
    # (derived from the conversation's prior last_message_at at inbound time).
    responded_within_10min: bool = False
    # S-09: behavioral latency signal (EMA from LeadRepository).
    lead_avg_latency_seconds: int | None = None
    # Text of the proactive/automation message the customer is replying to, when
    # the immediately-preceding turn was such a send. Injected as an authoritative
    # continuity directive in `_generate_and_deliver` so the bot continues the
    # automation's thread instead of restarting generically. None otherwise.
    proactive_reply_to: str | None = None
    # Vision attachment resolved for the current turn (customer sent a photo).
    # Loaded from storage in `generate_and_send_reply`; threaded into the
    # orchestrator so the model actually sees the image. None for text turns.
    current_image: ImagePart | None = None
    # Profilo di conversazione attivo (ADR 0022). Catturato in entrambi i punti
    # di costruzione del contesto — quello inline e quello del flush debounce —
    # perché è da qui che il resolver riceve il livello 0 della cascata. È anche
    # il timbro che finisce sulla risposta dell'assistente, così le statistiche
    # per profilo comprendono i turni conversazionali e non solo gli invii
    # proattivi.
    conv_profile_id: UUID | None = None


DEFAULT_SYSTEM_PROMPT = (
    "Sei un assistente conversazionale italiano per l'azienda. Rispondi in modo "
    "cortese, breve e professionale. Se la richiesta riguarda prenotazioni, proponi "
    "di prenotare. Se mancano informazioni critiche (nome, email, esigenza), "
    "chiedile in modo naturale, una alla volta. Non inventare fatti sull'azienda: "
    "se non sai qualcosa, dillo e offri di far contattare una persona."
)


def _default_system_prompt(*, booking_enabled: bool, lead_capture_enabled: bool) -> str:
    """The generic fallback prompt for a profile-less merchant, with the booking
    and lead-capture clauses gated by the playbook (ADR 0018). With both enabled
    (the default) it reproduces `DEFAULT_SYSTEM_PROMPT` byte-for-byte."""
    parts = [
        "Sei un assistente conversazionale italiano per l'azienda. Rispondi in modo "
        "cortese, breve e professionale."
    ]
    if booking_enabled:
        parts.append("Se la richiesta riguarda prenotazioni, proponi di prenotare.")
    if lead_capture_enabled:
        parts.append(
            "Se mancano informazioni critiche (nome, email, esigenza), chiedile in "
            "modo naturale, una alla volta."
        )
    parts.append(
        "Non inventare fatti sull'azienda: se non sai qualcosa, dillo e offri di far "
        "contattare una persona."
    )
    return " ".join(parts)


# Sentiment "positive" fragment without the booking upsell — used when the
# merchant has booking disabled (a pure info/reminder bot shouldn't push slots).
_SENTIMENT_POSITIVE_NO_BOOKING = (
    "Nota: il cliente sembra ben disposto e soddisfatto. Mantieni l'entusiasmo e "
    "asseconda l'apertura."
)

# Fail-safe reply: when the LLM turn errors out hard (both the primary model and
# the fallback failed, or any unexpected exception), the customer must still get
# something rather than silence — we send this and hand the thread to a human.
# Mirrors Amalia's `handle_ai_conversation_safe`.
# Entrambi i nomi dell'azione di handoff. L'ADR 0026 ha rinominato
# `escalate_human` in `handoff_human` e fa normalizzare il parse subito dopo la
# lettura, ma i consumatori a valle confrontavano ancora il nome storico: dopo la
# normalizzazione nessuno riconosceva più l'azione, quindi `claim_handoff` non
# veniva mai chiamato e il dispatcher scartava l'handler in silenzio
# (`action.no_handler`, livello debug). Confrontare contro l'insieme rende il
# percorso indifferente al nome, incluse le allowlist già salvate dai merchant.
_HANDOFF_ACTION_KINDS: frozenset[str] = frozenset({"handoff_human", "escalate_human"})

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


_WEEKDAYS_IT_CAP = (
    "Lunedì",
    "Martedì",
    "Mercoledì",
    "Giovedì",
    "Venerdì",
    "Sabato",
    "Domenica",
)


def _current_datetime_line(tz_name: str) -> str:
    """High-salience temporal-grounding line for the system prompt.

    The model has no other way to know today's date, and this bot's core job is
    date negotiation ("mattina o pomeriggio?", "giovedì va bene?"). Without this
    line the only date in the whole prompt was a hard-coded example from the
    action schema, actively misleading the model into the wrong year. Rendered in
    the merchant's timezone; weekday is locale-free (deterministic for tests).
    """
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = UTC
    now = datetime.now(tz=tz)
    weekday = _WEEKDAYS_IT_CAP[now.weekday()]
    return (
        f"Data e ora attuali: {weekday} {now.strftime('%d/%m/%Y')}, ore "
        f"{now.strftime('%H:%M')} (fuso orario {tz_name}). Usa SEMPRE questo "
        "riferimento per interpretare «oggi», «domani», «questa settimana», i "
        "giorni della settimana e gli orari: non dare mai per scontato un anno o "
        "un giorno diverso."
    )


async def build_cascade_system_prompt(
    *,
    session: Any,
    merchant_id: UUID,
    prior_sentiment: str | None = None,
    customer_message: str | None = None,
    profile_id: UUID | None = None,
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

    `profile_id` inserisce il livello 0 della cascata (ADR 0022): il profilo
    sovrascrive i knob di persona/tono e `bot.system_prompt_additions` sopra la
    configurazione del merchant, che resta condivisa (business info, KB,
    booking). Con `None` il prompt è byte-identico a prima dei profili — su cui
    esiste un golden test.
    """
    resolver = ConfigResolver(session)

    async def _str(key: ConfigKey) -> str | None:
        try:
            value = await resolver.resolve(key, merchant_id=merchant_id, profile_id=profile_id)
        except Exception:
            return None
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    async def _list(key: ConfigKey) -> list[str]:
        try:
            value = await resolver.resolve(key, merchant_id=merchant_id, profile_id=profile_id)
        except Exception:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []

    async def _examples(key: ConfigKey) -> list[tuple[str, str]]:
        try:
            value = await resolver.resolve(key, merchant_id=merchant_id, profile_id=profile_id)
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
            value = await resolver.resolve(key, merchant_id=merchant_id, profile_id=profile_id)
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
    tz_name = await _str(ConfigKey.SCHEDULE_TIMEZONE) or "Europe/Rome"
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
    # Playbook capability gates (ADR 0018) — default True keeps today's prompt.
    booking_enabled = await _bool(ConfigKey.BOOKING_ENABLED, default=True)
    lead_capture_enabled = await _bool(ConfigKey.LEAD_CAPTURE_ENABLED, default=True)

    # Store policies — short, always-relevant facts injected straight into
    # the prompt (no RAG). Best-effort: a missing row / error yields no lines.
    policy_lines = await build_store_policy_lines(session, merchant_id)

    # Servizi prenotabili — iniettati nel prompt affinché l'agente sappia cosa
    # offrire, la durata e il prezzo. Best-effort: ignora errori DB.
    bookable_services: list[Any] = []
    try:
        bookable_services = await ServiceRepository(session).list(merchant_id)
    except Exception:
        pass

    # Orari di apertura strutturati (tabella business_hours). Best-effort.
    structured_hours: list[Any] = []
    try:
        structured_hours = await BusinessHourRepository(session).list(merchant_id)
    except Exception:
        pass

    # Chiusure eccezionali future (festivi, ferie, ponti). Best-effort.
    import datetime as _dt

    upcoming_closures: list[Any] = []
    try:
        upcoming_closures = await BusinessClosureRepository(session).list(
            merchant_id, from_date=_dt.date.today()
        )
    except Exception:
        pass

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
            bookable_services,
            structured_hours,
            upcoming_closures,
        ]
    )
    if not has_profile:
        return _default_system_prompt(
            booking_enabled=booking_enabled, lead_capture_enabled=lead_capture_enabled
        )

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

    # Temporal grounding, high in the prompt: the bot negotiates dates/times and
    # otherwise has no idea what day it is.
    lines.append(_current_datetime_line(tz_name))

    if description:
        lines.append(f"L'attività si descrive così: {description}")
    if offer:
        lines.append(f"Offerta principale: {offer}")
    if pricing_notes:
        lines.append(f"Note sui prezzi: {pricing_notes}")
    if hours:
        lines.append(f"Orari: {hours}")
    if bookable_services and booking_enabled:
        svc_lines = ["Servizi prenotabili (usa il campo service_id nell'azione book_slot):"]
        for svc in bookable_services:
            price_str = f"€{svc.price}" if svc.price is not None else "prezzo su richiesta"
            desc_str = f" — {svc.description}" if svc.description else ""
            svc_lines.append(
                f"- {svc.name} (id: {svc.id}, durata: {svc.duration_min} min, {price_str}{desc_str})"
            )
        lines.append("\n".join(svc_lines))
    if structured_hours:
        _DAY_NAMES = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
        h_lines = ["Orario di apertura:"]
        for row in structured_hours:
            day = _DAY_NAMES[row.day_of_week]
            if not row.is_open or row.open_time is None or row.close_time is None:
                h_lines.append(f"- {day}: Chiuso")
            else:
                slot = f"{row.open_time.strftime('%H:%M')}-{row.close_time.strftime('%H:%M')}"
                if row.break_start and row.break_end:
                    slot += f" (pausa {row.break_start.strftime('%H:%M')}-{row.break_end.strftime('%H:%M')})"
                h_lines.append(f"- {day}: {slot}")
        lines.append("\n".join(h_lines))
    if upcoming_closures:
        cl_lines = ["Chiusure eccezionali in programma:"]
        for c in upcoming_closures[:10]:
            label = f" — {c.label}" if c.label else ""
            cl_lines.append(f"- {c.closed_on.strftime('%d/%m/%Y')}{label}")
        lines.append("\n".join(cl_lines))
    if location:
        lines.append(f"Sede / area di copertura: {location}")
    if website:
        lines.append(f"Sito web: {website}")
    if policy_lines:
        lines.append("Politiche del negozio:\n" + "\n".join(f"- {p}" for p in policy_lines))

    # Tone-of-address: structured formality wins; "auto" keeps the legacy tone.
    tone_clause = _FORMALITY_FRAGMENTS.get(formality) or f"Mantieni un tono {tone}."
    # Lead-capture sentence gated by the playbook (ADR 0018). With capture on
    # (default) the concrete clause is byte-identical to today's.
    lead_capture_sentence = (
        " Se mancano informazioni critiche (nome, email, esigenza), chiedile una alla volta."
        if lead_capture_enabled
        else ""
    )
    concrete_clause = (
        "Sii breve, cortese e concreto." + lead_capture_sentence + " Non inventare fatti "
        "sull'attività: se non sai qualcosa, dillo e offri di far contattare una persona."
    )
    style_bits = [
        # Istruzione forte e prioritaria: senza enfasi il modello tende a
        # rispecchiare la lingua del cliente ignorando questa direttiva.
        f"REGOLA ASSOLUTA DI LINGUA: scrivi OGNI risposta esclusivamente in "
        f"lingua «{language}», qualunque sia la lingua usata dal cliente. Non "
        f"cambiare mai lingua, nemmeno se il cliente scrive in un'altra.",
        tone_clause,
        _VERBOSITY_FRAGMENTS.get(verbosity, _VERBOSITY_FRAGMENTS["equilibrato"]),
        _EMOJI_FRAGMENTS.get(emoji_policy, _EMOJI_FRAGMENTS["sobrio"]),
        concrete_clause,
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
    # latency). neutral/None inject nothing. The "positive" fragment's booking
    # upsell is dropped when booking is disabled (ADR 0018).
    if sentiment_adaptation and prior_sentiment in _SENTIMENT_FRAGMENTS:
        frag = _SENTIMENT_FRAGMENTS[prior_sentiment]
        if prior_sentiment == "positive" and not booking_enabled:
            frag = _SENTIMENT_POSITIVE_NO_BOOKING
        lines.append(frag)

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
        media_pipeline: MediaPipeline | None = None,
        kek_base64: str,
    ) -> None:
        self._orchestrator = orchestrator
        self._dispatcher = action_dispatcher
        self._sender = reply_sender
        self._embedder = embedder
        self._sentiment = sentiment
        # Inbound-media download + storage (+ audio transcription). None in tests
        # and when media support is unconfigured → media turns degrade to text.
        self._media_pipeline = media_pipeline
        # Read-only tool executor for the Amalia-style tool-use loop. When wired
        # (+ AGENT_TOOL_USE_ENABLED) the orchestrator can ground itself on live
        # availability/appointment data mid-turn. None = single-shot turns.
        self._tool_executor = tool_executor
        self._kek = kek_base64
        # Cached nano LLM client for RAG HyDE + re-ranking (low-cost operations).
        self._rag_nano_client: Any = None

    async def handle_inbound(
        self,
        *,
        phone_number_id: str,
        from_phone: str,
        text: str,
        wa_message_id: str | None,
        wa_timestamp_unix: int | None = None,
        media: dict[str, Any] | None = None,
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
            media=media,
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
        media: dict[str, Any] | None = None,
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

            conv = await convs.get_active_or_reopen_latest(
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
            history = await msgs.list_history(conv.id, limit=_HISTORY_FETCH_LIMIT)
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
            # needs-human, and notify — no LLM turn. Exactly-once: a burst of
            # media must not re-stamp the handoff nor re-notify the operator on
            # every file — only the first one flips the thread.
            if force_handoff_reason:
                auto_reply_on = False
                # Atomic claim rather than read-check-write on `conv`: a 10-video
                # album fans out to concurrent jobs that all read `auto_reply =
                # true` before any of them commits, so the old check let every
                # one of them emit `conversation.escalated` — N operator
                # notifications for one handoff. `WHERE auto_reply = true` lets
                # exactly one through.
                claimed = await ConversationRepository(session).claim_handoff(
                    conv.id, reason=force_handoff_reason, summary=None
                )
                if claimed:
                    # Keep the in-session ORM copy consistent with the row the
                    # claim just wrote — later code in this transaction reads it.
                    conv.auto_reply = False
                    conv.handoff_at = datetime.now(UTC)
                    conv.handoff_reason = force_handoff_reason
                    conv.handoff_resolved_at = None
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
                # Media descriptor (image/audio/video/document/sticker). Persisted
                # under `meta.media` with `storage_path=null`; filled by the
                # best-effort download below. The row is written first so a failed
                # download still leaves an inbox bubble (Amalia's two-phase shape).
                message_meta: dict[str, Any] | None = None
                if media and media.get("id"):
                    message_meta = {
                        "media": {
                            "kind": media.get("kind"),
                            "mime": media.get("mime"),
                            "wa_media_id": media.get("id"),
                            "caption": media.get("caption"),
                            "storage_path": None,
                        }
                    }
                # Attribuzione last-touch (0047): a quale invio in uscita sta
                # rispondendo questo messaggio. Va risolto PRIMA dell'INSERT,
                # perché la regola guarda l'ultimo messaggio della conversazione
                # e dopo l'inserimento l'ultimo sarebbe questo stesso. Una query
                # servita da `ix_messages_conv_created`; se il thread è già in
                # entrata torna None e non si attribuisce niente.
                reply_target = await msgs.resolve_reply_target(conv.id)
                persisted = await msgs.persist_user_message(
                    conversation_id=conv.id,
                    merchant_id=resolved.merchant_id,
                    content=text,
                    wa_message_id=wa_message_id,
                    variant_id=conv.variant_id,
                    reply_to_message_id=reply_target.id if reply_target else None,
                    profile_id=conv.profile_id,
                    meta=message_meta,
                )
                # Download + store the media (best-effort) and patch the row's
                # `meta.media`. Never raises — a media failure must not lose the
                # customer's turn nor block the reply. Images download sub-second;
                # video/document (handed off) are the only slow case.
                if message_meta is not None and wa_message_id and self._media_pipeline is not None:
                    try:
                        patch = await self._media_pipeline.fetch_and_store(
                            api_key=resolved.api_key,
                            waba_base_url=resolved.waba_base_url,
                            phone_number_id=phone_number_id,
                            merchant_id=resolved.merchant_id,
                            conversation_id=conv.id,
                            message_id=persisted.id,
                            media_id=str(media["id"]),
                            kind=str(media.get("kind") or ""),
                            mime=media.get("mime"),
                        )
                        if patch:
                            await msgs.patch_message_media(
                                wa_message_id=wa_message_id,
                                merchant_id=resolved.merchant_id,
                                media_patch=patch,
                            )
                    except Exception as e:  # pragma: no cover - best effort
                        logger.warning(
                            "uc01.media_pipeline_failed",
                            error=str(e),
                            wa_message_id=wa_message_id,
                        )
                # S-03: capture behavioral signals (latency + message length)
                latency_s: int | None = None
                if prior_last_message_at is not None:
                    delta = datetime.now(UTC) - (
                        prior_last_message_at.replace(tzinfo=UTC)
                        if prior_last_message_at.tzinfo is None
                        else prior_last_message_at
                    )
                    latency_s = max(0, int(delta.total_seconds()))
                lead_repo = LeadRepository(session)
                await lead_repo.update_behavioral_signals(
                    lead.id,
                    response_latency_s=latency_s,
                    message_length=len(text),
                )
                # S-05: compute intake score on the very first inbound message.
                if prior_last_message_at is None:
                    intake = _compute_intake_score(text)
                    await lead_repo.update_intake_score(lead.id, score=intake)
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
                    profile_id=conv.profile_id,
                    properties=received_props,
                )
            await convs.touch_last_message(conv.id)

            # Capture scalars + the prepared history while the session is open;
            # the ORM objects detach after the commit below.
            conv_id = conv.id
            conv_variant_id = conv.variant_id
            conv_profile_id = conv.profile_id
            conv_context_summary = conv.context_summary
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
            conv_current_state = conv.current_state
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
                conv_context_summary=conv_context_summary,
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
                conv_current_state=conv_current_state,
                conv_profile_id=conv_profile_id,
                latest_wa_message_id=wa_message_id,
                proactive_reply_to=_trailing_proactive_text(history),
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

            # Re-evaluate the auto-reply gate at SEND time. State can change
            # during the debounce window (operator takeover flips conv.auto_reply,
            # a STOP/CANCELLA opt-out sets opted_out_at, a soft-pause sets
            # ai_disabled_until) and the reply must respect the latest state, not
            # the snapshot taken when the inbound was first persisted. Without
            # this the bot can talk over a human who just took over or reply to a
            # lead who just unsubscribed.
            merchant_auto_reply = await self._resolve_bool(
                session, resolved.merchant_id, ConfigKey.BOT_AUTO_REPLY_ENABLED, default=False
            )
            soft_paused = (
                conv.ai_disabled_until is not None and conv.ai_disabled_until > datetime.now(UTC)
            )
            if (
                not merchant_auto_reply
                or not conv.auto_reply
                or lead.opted_out_at is not None
                or soft_paused
            ):
                logger.info(
                    "uc01.reply_suppressed_at_flush",
                    conversation_id=str(conv.id),
                    merchant_id=str(resolved.merchant_id),
                    merchant_off=not merchant_auto_reply,
                    thread_off=not conv.auto_reply,
                    opted_out=lead.opted_out_at is not None,
                    soft_paused=soft_paused,
                )
                return InboundResult(handled=False, reason="auto_reply_off")

            history = await msgs.list_history(conv.id, limit=_HISTORY_FETCH_LIMIT)
            history = [m for m in history if m.wa_message_id not in exclude]

            # Vision / voice: attach the current turn's image for the model to see,
            # or swap in the audio transcription as the effective turn text.
            current_image, effective_text = await self._resolve_current_media(
                msgs, wa_message_id, text
            )

            rc = _ReplyContext(
                resolved=resolved,
                conv_id=conv.id,
                conv_variant_id=conv.variant_id,
                conv_context_summary=conv.context_summary,
                lead_id=lead.id,
                lead_score=lead.score,
                lead_name=lead.name,
                lead_email=lead.email,
                lead_sentiment=lead.sentiment,
                lead_pipeline_stage_id=lead.pipeline_stage_id,
                chat_history=_to_chat_history(history),
                from_phone=from_phone,
                phone_number_id=phone_number_id,
                text=effective_text,
                conv_current_state=conv.current_state,
                conv_profile_id=conv.profile_id,
                latest_wa_message_id=wa_message_id,
                lead_avg_latency_seconds=lead.avg_response_latency_seconds,
                proactive_reply_to=_trailing_proactive_text(history),
                current_image=current_image,
            )
        return await self._generate_and_deliver(rc)

    async def _resolve_current_media(
        self, msgs: MessageRepository, wa_message_id: str | None, text: str
    ) -> tuple[ImagePart | None, str]:
        """Resolve the current turn's vision/voice payload from persisted media.

        Returns `(image, effective_text)`:
        - image message → load the bytes for a vision block, text unchanged;
        - audio message → no image, text replaced by the transcription (so the
          model reads the voice note) when one is present;
        - anything else / no pipeline → `(None, text)`.

        Best-effort: any failure degrades to the text turn. The current inbound
        is excluded from history but persisted, so it's fetched by wa id here.
        """
        if not wa_message_id or self._media_pipeline is None:
            return None, text
        try:
            cur = await msgs.find_by_wa_message_id(wa_message_id)
        except Exception:  # pragma: no cover - defensive
            return None, text
        media = (cur.meta or {}).get("media") if cur is not None else None
        if not media:
            return None, text
        kind = media.get("kind")
        if kind == "audio":
            transcription = media.get("transcription")
            return None, (transcription or text)
        if kind == "image" and media.get("storage_path"):
            try:
                img = await self._media_pipeline.load_image(
                    storage_path=str(media["storage_path"]), mime=media.get("mime")
                )
            except Exception:  # pragma: no cover - best effort
                img = None
            return img, text
        return None, text

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
                profile_id=rc.conv_profile_id,
            )

            # Inject already-known lead data so the bot personalizes without re-asking.
            if rc.lead_name or rc.lead_email:
                known_parts = []
                if rc.lead_name:
                    known_parts.append(f"nome: {rc.lead_name}")
                if rc.lead_email:
                    known_parts.append(f"email: {rc.lead_email}")
                system_prompt += (
                    f"\n\nDati già noti su questo cliente: {', '.join(known_parts)}. "
                    "Usali per personalizzare la risposta (es. chiama il cliente per nome) "
                    "senza chiedere di nuovo informazioni già raccolte."
                )

            kb_chunks = []
            try:
                # Small KB → inject the whole thing into the prompt (no retrieval,
                # so no min_score/embedding miss can hide a fact that IS in the KB).
                # Large KB → fall back to RAG. Threshold is a token estimate.
                kb_tokens = await kb_estimated_tokens(session, resolved.merchant_id)
                if 0 < kb_tokens < KB_INLINE_MAX_TOKENS:
                    kb_chunks = await kb_all_chunks(session, resolved.merchant_id)
                    logger.info(
                        "uc01.kb_inline", merchant_id=str(resolved.merchant_id), tokens=kb_tokens
                    )
                elif self._embedder is not None:
                    async with session.begin_nested():
                        top_k = await self._resolve_int(
                            session, resolved.merchant_id, ConfigKey.RAG_TOP_K, default=5
                        )
                        min_score = await self._resolve_float(
                            session, resolved.merchant_id, ConfigKey.RAG_MIN_SCORE, default=0.7
                        )
                        hyde_enabled = await self._resolve_bool(
                            session, resolved.merchant_id, ConfigKey.RAG_HYDE_ENABLED, default=True
                        )
                        rerank_enabled = await self._resolve_bool(
                            session,
                            resolved.merchant_id,
                            ConfigKey.RAG_RERANK_ENABLED,
                            default=True,
                        )
                        rerank_top_k = await self._resolve_int(
                            session, resolved.merchant_id, ConfigKey.RAG_RERANK_TOP_K, default=5
                        )
                        freshness_decay = await self._resolve_float(
                            session,
                            resolved.merchant_id,
                            ConfigKey.RAG_FRESHNESS_DECAY,
                            default=0.01,
                        )
                        rag = RAGEngine(session, self._embedder, llm_client=self._rag_llm_client())
                        kb_chunks = await rag.retrieve(
                            rc.text,
                            merchant_id=resolved.merchant_id,
                            top_k=top_k,
                            min_score=min_score,
                            hyde_enabled=hyde_enabled,
                            rerank_enabled=rerank_enabled,
                            rerank_top_k=rerank_top_k,
                            freshness_decay=freshness_decay,
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

            # Playbook runtime (ADR 0018) — resolved once; gates the FSM hint,
            # scoring/pipeline side effects, action allowlist and directives.
            # Defaults reproduce today's sales behavior.
            # Il profilo attivo è il livello 0 della cascata (ADR 0022): è
            # esattamente il playbook — obiettivo, direttive, azioni permesse,
            # `mode` — che un profilo modula.
            caps = await resolve_playbook_runtime(
                session, resolved.merchant_id, profile_id=rc.conv_profile_id
            )

            # FSM: load current state, inject hint into system prompt. Suppress the
            # GREETING "accogli il lead per la prima volta / presentati" hint when the
            # customer is actually replying to a proactive/automation message: the lead
            # was already engaged by the business, so a from-scratch greeting is wrong —
            # it was the main driver of the bot ignoring the automation's thread and
            # pivoting to a generic "come posso aiutarti". When the playbook disables
            # the FSM (mode "off") no per-turn state hint is injected at all.
            fsm_state = (
                ConvState(rc.conv_current_state) if rc.conv_current_state else ConvState.GREETING
            )
            if not caps.fsm_enabled:
                fsm_hint = ""
            else:
                fsm_hint = (
                    ""
                    if (rc.proactive_reply_to and fsm_state == ConvState.GREETING)
                    else state_system_hint(fsm_state)
                )
            if fsm_hint:
                system_prompt = system_prompt + "\n\n" + fsm_hint

            # S-09: proactive escalation risk — inject empathy hint when risk ≥ 60
            from ai_core.escalation_predictor import predict_escalation_risk
            from sqlalchemy import func, select as sa_select
            from db.models import Objection as ObjModel

            try:
                async with session.begin_nested():
                    obj_count_row = await session.execute(
                        sa_select(func.count())
                        .select_from(ObjModel)
                        .where(ObjModel.conversation_id == rc.conv_id)
                    )
                    obj_count = obj_count_row.scalar() or 0
            except Exception:
                obj_count = 0

            recent_user_msgs = [m.content for m in rc.chat_history[-6:] if m.role == "user"]
            esc_risk = predict_escalation_risk(
                turn_count=len(rc.chat_history),
                lead_score=rc.lead_score,
                hot_threshold=advance_threshold,
                sentiment=rc.lead_sentiment,
                objection_count=obj_count,
                recent_messages=recent_user_msgs,
                avg_response_latency_seconds=rc.lead_avg_latency_seconds,
            )
            if esc_risk.score >= 60:
                system_prompt = (
                    system_prompt
                    + "\n\nATTENZIONE: il lead mostra segnali di frustrazione o tensione. "
                    "Sii particolarmente empatico, paziente e rassicurante in questa risposta."
                )
                if esc_risk.score >= 75:
                    await analytics.emit(
                        tenant_id=resolved.tenant_id,
                        merchant_id=resolved.merchant_id,
                        event_type="escalation.risk_high",
                        subject_type="conversation",
                        subject_id=rc.conv_id,
                        properties={"risk_score": esc_risk.score, "factors": esc_risk.factors},
                    )

            # Continuity with proactive/automation sends. The message the customer is
            # replying to is already in the LLM history as an `assistant` turn, but a
            # bare assistant turn gets steam-rolled by the merchant's generic persona
            # (and, at GREETING, by the "presentati" FSM hint): the bot restarts
            # generically instead of continuing what the automation set up — reported
            # as "l'AI non considera i messaggi dell'automazione". Re-inject that
            # message as an explicit, AUTHORITATIVE continuity directive. Injected LAST
            # (after the persona, FSM and escalation hints) so it wins on salience.
            if rc.proactive_reply_to:
                proactive_text = rc.proactive_reply_to[:_PROACTIVE_CONTEXT_MAX_CHARS]
                system_prompt += (
                    "\n\nCONTINUITÀ CON L'AUTOMAZIONE (istruzione prioritaria): questo "
                    "NON è un primo contatto. L'ultimo messaggio ricevuto da questo "
                    "contatto è stato inviato automaticamente dall'attività ed è il "
                    f"seguente:\n«{proactive_text}»\n"
                    "Il cliente sta rispondendo proprio a QUESTO messaggio. NON "
                    "presentarti da capo, NON ripartire con una presentazione generica o "
                    "un «come posso aiutarti», NON cambiare argomento e NON proporre temi "
                    "non attinenti (es. prenotazioni o servizi) se quel messaggio "
                    "riguardava altro. Dai continuità a ciò che vi è stato chiesto o "
                    "proposto: interpreta la sua risposta in quel contesto (es. «appena "
                    "inviato», «fatto», «ok» si riferiscono all'azione richiesta lì) e "
                    "prosegui il flusso già avviato."
                )

            # S-04: context compression — keep a running summary of the turns that
            # have scrolled out of the verbatim window so long/resumed threads
            # don't silently lose their early context. The history fetch
            # (_HISTORY_FETCH_LIMIT) is deliberately wider than the threshold so
            # this branch is reachable; the summary ACCUMULATES (each
            # recompression folds the previous summary back in) so facts older
            # than the fetch window survive across turns.
            effective_history = rc.chat_history
            compress_threshold = await self._resolve_int(
                session,
                resolved.merchant_id,
                ConfigKey.AGENT_CONTEXT_COMPRESS_THRESHOLD,
                default=30,
            )
            # Clamp so compression stays reachable even if a merchant configures a
            # threshold at/above the fetch window — otherwise it silently degrades
            # back to plain truncation for that tenant.
            compress_threshold = min(compress_threshold, _HISTORY_FETCH_LIMIT - _KEEP_RECENT)

            saved_block: MemoryBlock | None = None
            if rc.conv_context_summary and rc.conv_context_summary.get("text"):
                _saved = rc.conv_context_summary
                saved_block = MemoryBlock(
                    text=_saved["text"],
                    compressed_turns=int(_saved.get("compressed_turns", 0)),
                    compressed_at=_saved.get("compressed_at", ""),
                )

            if len(effective_history) > compress_threshold:
                nano_client = self._rag_llm_client()
                if nano_client is not None:
                    compressor = ContextCompressor(nano_client)
                    block = await compressor.compress(
                        effective_history,
                        prior_summary=saved_block.text if saved_block else None,
                    )
                    if block is not None:
                        await convs.save_context_summary(
                            rc.conv_id,
                            {
                                "text": block.text,
                                "compressed_turns": block.compressed_turns,
                                "compressed_at": block.compressed_at,
                            },
                        )
                        saved_block = block
                # Feed [summary] + the most recent turns verbatim. If the nano
                # client is unavailable and no prior summary exists, fail open and
                # keep the full fetched window (more context, never less).
                if saved_block is not None:
                    effective_history = [
                        memory_block_as_message(saved_block),
                        *effective_history[-_KEEP_RECENT:],
                    ]
            elif saved_block is not None:
                # Short window (e.g. after message deletion) but an older summary
                # exists — reinject it ahead of the verbatim turns.
                effective_history = [memory_block_as_message(saved_block), *effective_history]

            ctx = ConversationContext(
                merchant_id=resolved.merchant_id,
                tenant_id=resolved.tenant_id,
                lead_id=rc.lead_id,
                lead_score=rc.lead_score,
                hot_threshold=hot_threshold,
                system_prompt=system_prompt,
                history=effective_history,
                kb_chunks=kb_chunks,
                variant_id=rc.conv_variant_id,
                advance_threshold=advance_threshold,
                allowed_actions=caps.allowed_actions,
                scoring_enabled=caps.scoring_enabled,
                directives=caps.directives,
                critical_keywords=caps.critical_keywords,
                current_image=rc.current_image,
                assistant_name=await self._resolve_optional_str(
                    session, resolved.merchant_id, ConfigKey.BOT_ASSISTANT_NAME
                ),
                handoff=await self._resolve_handoff_prompt(
                    session, resolved.merchant_id, profile_id=rc.conv_profile_id
                ),
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
                    # S-04: coherence guard — retry once if the reply contradicts prior facts
                    coherence_enabled = await self._resolve_bool(
                        session,
                        resolved.merchant_id,
                        ConfigKey.AGENT_COHERENCE_GUARD_ENABLED,
                        default=True,
                    )
                    if coherence_enabled:
                        nano_client = self._rag_llm_client()
                        if nano_client is not None:
                            guard = CoherenceGuard(nano_client)
                            result = await guard.check(effective_history, response.reply_text)
                            if not result.coherent:
                                logger.info(
                                    "uc01.coherence_retry",
                                    issue=result.issue,
                                    conversation_id=str(rc.conv_id),
                                )
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
                            session, resolved.merchant_id, ConfigKey.HANDOFF_MESSAGE
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

            # Empty-reply guard. Two real sources land here, neither of which
            # raises: `_parse_structured` now yields an empty reply_text instead
            # of pasting an unusable JSON blob to the customer, and OpenAI itself
            # returns `content=None` (→ "") on a content filter or when a
            # reasoning model spends its whole budget on reasoning. An empty
            # bubble is rejected by WhatsApp and would burn the job's retries, so
            # route it through the same courtesy + handoff fail-safe as a hard
            # LLM error rather than letting silence reach the customer.
            # One retry first: an unusable turn is nearly always a sampling
            # artefact and a second draw clears it. Same shape as the coherence
            # guard's retry above, and it keeps the terminal path below rare.
            if not llm_failed and not response.reply_text.strip():
                logger.warning("uc01.empty_reply_retry", conversation_id=str(rc.conv_id))
                try:
                    response = await self._run_orchestrator(session, ctx, rc)
                except Exception as e:
                    logger.error(
                        "uc01.empty_reply_retry_failed",
                        conversation_id=str(rc.conv_id),
                        error=str(e),
                    )

            if not llm_failed and not response.reply_text.strip():
                logger.error(
                    "uc01.empty_reply",
                    conversation_id=str(rc.conv_id),
                    merchant_id=str(resolved.merchant_id),
                    model=response.model,
                )
                # Deliberately NOT `llm_failed = True`. That flag means "the LLM
                # call itself blew up", and downstream it overrides two merchant
                # settings: it bypasses `escalation.enabled = False` (line ~1742)
                # and `silent_handoff` (~1771). Hijacking it here would let a
                # single malformed turn call `claim_handoff`, which sets
                # `auto_reply = false` for good — on an agency that switched
                # escalation off precisely because nobody watches the inbox, the
                # thread would go permanently mute. Emitting the action normally
                # lets those settings decide, as they do for any escalation.
                response = OrchestratorResponse(
                    reply_text=(
                        await self._resolve_optional_str(
                            session, resolved.merchant_id, ConfigKey.HANDOFF_MESSAGE
                        )
                        or _LLM_FAILURE_MESSAGE
                    ),
                    actions=[
                        OrchestratorAction(
                            kind="escalate_human",
                            payload={
                                "reason": "empty_reply",
                                "customer_message_summary": (
                                    "L'assistente AI non ha prodotto una risposta "
                                    "utilizzabile: serve un operatore umano."
                                ),
                            },
                        )
                    ],
                    model=response.model,
                    tokens_in=response.tokens_in,
                    tokens_out=response.tokens_out,
                    latency_ms=response.latency_ms,
                )

            # Handoff reply policy: when the bot escalates to a human the merchant
            # can force a fixed message (handoff_message) or hand off silently
            # (no customer-facing reply). State/scoring/dispatch still run.
            # The handoff message goes out EXACTLY ONCE per handoff: concurrent
            # turns (es. un album di foto fanned out to parallel jobs) race on an
            # atomic claim, and only the winner speaks. On a hard LLM failure we
            # never suppress the winning reply — the customer must get a reply
            # even if silent-handoff is configured.
            suppress_reply = False
            handoff_claimed = False
            escalate_action = next(
                (a for a in response.actions if a.kind in _HANDOFF_ACTION_KINDS), None
            )
            if escalate_action is not None:
                # One escalation per turn. A model that repeats the action in the
                # same JSON would otherwise dispatch the handler twice — two
                # `conversation.escalated` rows, two Slack pings for one handoff
                # (the dispatcher dedupes per event id, which does not help here).
                response.actions = [
                    a
                    for a in response.actions
                    if a.kind not in _HANDOFF_ACTION_KINDS or a is escalate_action
                ]
                escalation_enabled = await self._resolve_bool(
                    session, resolved.merchant_id, ConfigKey.HANDOFF_ENABLED, default=True
                )
                if not escalation_enabled and not llm_failed:
                    # Escalation locked off by the agency: the thread stays on the
                    # bot (the handler would skip the takeover anyway), so sending
                    # the handoff copy would promise an operator who never comes —
                    # and would repeat on every following inbound. Drop the action
                    # and let the LLM's own reply go out.
                    response.actions = [a for a in response.actions if a.kind not in _HANDOFF_ACTION_KINDS]
                elif not await convs.claim_handoff(
                    rc.conv_id,
                    reason=escalate_action.payload.get("reason"),
                    summary=escalate_action.payload.get("customer_message_summary"),
                ):
                    # Lost the claim: another turn already handed this thread off
                    # and the customer already received the handoff message. Stay
                    # silent and drop the action so the operator isn't re-notified.
                    suppress_reply = True
                    response.actions = [a for a in response.actions if a.kind not in _HANDOFF_ACTION_KINDS]
                else:
                    handoff_claimed = True
                    silent = await self._resolve_bool(
                        session,
                        resolved.merchant_id,
                        ConfigKey.HANDOFF_SILENT,
                        default=False,
                    )
                    if silent and not llm_failed:
                        suppress_reply = True
                    elif not llm_failed:
                        handoff_message = await self._resolve_optional_str(
                            session, resolved.merchant_id, ConfigKey.HANDOFF_MESSAGE
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

            # FSM: transition and persist new state. Skipped when the playbook
            # disables the FSM (mode "off") — the state column is left untouched.
            if caps.fsm_enabled:
                new_fsm_state = next_state(
                    fsm_state, response.actions, turn_count=len(rc.chat_history)
                )
                if new_fsm_state != fsm_state:
                    await convs.update_state(rc.conv_id, new_fsm_state.value)

            _out_msg = None
            if not suppress_reply:
                _out_msg = await msgs.persist_assistant_message(
                    conversation_id=rc.conv_id,
                    merchant_id=resolved.merchant_id,
                    content=response.reply_text,
                    model=response.model,
                    tokens_in=response.tokens_in,
                    tokens_out=response.tokens_out,
                    latency_ms=response.latency_ms,
                    variant_id=rc.conv_variant_id,
                    profile_id=rc.conv_profile_id,
                )
                await convs.touch_last_message(rc.conv_id)

                await analytics.emit(
                    tenant_id=resolved.tenant_id,
                    merchant_id=resolved.merchant_id,
                    event_type="message.replied",
                    subject_type="conversation",
                    subject_id=rc.conv_id,
                    variant_id=rc.conv_variant_id,
                    profile_id=rc.conv_profile_id,
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

        _last_wamid: str | None = None
        i = 0
        try:
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
                # WhatsApp dismisses "typing…" the moment a message goes out, so
                # the pre-loop call above only covers the first bubble — without
                # this the pauses before bubbles 2..N are silent. Re-arming keeps
                # the visible typing time proportional to each bubble's length.
                if i > 0 and delay > 0 and typing_indicator_enabled and rc.latest_wa_message_id:
                    await self._maybe_send_typing(rc, rc.latest_wa_message_id)
                if delay > 0:
                    await asyncio.sleep(delay)
                _last_wamid = await self._sender.send(
                    phone_number_id=rc.phone_number_id,
                    api_key=resolved.api_key,
                    to_phone=rc.from_phone,
                    text=bubble,
                    waba_base_url=resolved.waba_base_url,
                )
        except Exception as e:
            # The row is still `pending`; leaving it there tells the merchant's
            # inbox a reply went out that never did. Record the real outcome,
            # then re-raise so the job's retry semantics are unchanged.
            logger.error(
                "uc01.send_failed",
                conversation_id=str(rc.conv_id),
                merchant_id=str(resolved.merchant_id),
                error=str(e),
                bubble_index=i,
                bubbles=len(bubbles),
            )
            if _out_msg is not None:
                # Multi-bubble is on by default (delivery.multi_bubble_max = 2),
                # so a failure at i > 0 means the customer has already read the
                # opening bubble. The row carries the WHOLE reply, so calling it
                # `failed` would be as wrong as the old unconditional `sent`:
                # record it as delivered-but-incomplete instead. Only a failure
                # on the first bubble means nothing reached the customer.
                await self._mark_message_failed(
                    _out_msg.id,
                    code="send_failed" if i == 0 else "partial_send",
                    detail=str(e),
                    extra={"bubble_index": i, "bubbles": len(bubbles)},
                    status="failed" if i == 0 else "sent",
                )
            raise

        # Promote on any completed send loop, not just when the sender handed
        # back an id: keying this on `_last_wamid` would strand the row in
        # `pending` whenever the provider returns no id.
        if _out_msg is not None and bubbles:
            from db import session_scope
            from db.models.conversation import Message as _Message
            from sqlalchemy import update as _update

            values: dict[str, Any] = {"status": "sent"}
            if _last_wamid:
                values["wa_message_id"] = _last_wamid
            async with session_scope() as _s:
                await _s.execute(
                    _update(_Message).where(_Message.id == _out_msg.id).values(**values)
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
            # The reply policy above already claimed (and only left the action in
            # place if it won), so the handler must not claim a second time.
            handoff_claimed=handoff_claimed,
        )

        # UC-05 — always-on cumulative scoring. Derive behavioural signals from
        # accumulated state (name/email on file, engagement, sentiment, booking
        # intent) and merge with any content signals the LLM reported this turn,
        # then ensure exactly one update_score action carries the merged set.
        # Skipped entirely when the playbook disables scoring (ADR 0018): no
        # update_score is synthesized (the bot doesn't qualify leads).
        merged_signals: dict[str, bool] = {}
        if caps.scoring_enabled:
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
        else:
            actions = list(response.actions)

        # UC-04 — deterministic pipeline advancement. The LLM may optionally emit
        # move_pipeline, but we don't rely on it: if the (this-turn) score crosses
        # the merchant's advance_threshold and the lead isn't already in the
        # qualified stage, inject a move_pipeline action ourselves so the
        # advancement is repeatable and not at the model's discretion. Gated off
        # by the playbook (pipeline.auto_advance) for non-sales bots.
        if caps.pipeline_auto_advance:
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

    def _rag_llm_client(self) -> Any:
        """Return a cheap nano LLM client for RAG HyDE + re-ranking.

        Lazily built from the orchestrator's router. Returns None if the router
        doesn't expose a nano model (safe: RAGEngine falls back to raw query).
        """
        if self._rag_nano_client is not None:
            return self._rag_nano_client
        try:
            from ai_core.llm import OpenAIClient
            from ai_core.router import ModelRouter

            router: ModelRouter = self._orchestrator._router
            # ModelRouter has no `_api_key`; the key lives on its Settings. Using
            # the wrong attr made this raise AttributeError → caught below → None,
            # so HyDE + re-ranking silently never ran (raw-query retrieval only).
            client = OpenAIClient(model="gpt-4.1-nano", api_key=router._settings.openai_api_key)
            self._rag_nano_client = client
            return client
        except Exception:
            return None

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
        # Mirror SYSTEM_DEFAULTS (3), not 1: this default only applies when the
        # resolver itself fails, and 1 iteration means the loop breaks before
        # executing anything — tool-use on with a guaranteed dead end, the worst
        # of both. Degrade to the configured behavior instead.
        max_iter = await self._resolve_int(
            session, ctx.merchant_id, ConfigKey.AGENT_MAX_TOOL_ITERATIONS, default=3
        )
        return await self._orchestrator.run(
            ctx,
            rc.text,
            tool_executor=self._tool_executor,
            max_iterations=max(1, max_iter),
        )

    async def _mark_message_failed(
        self,
        message_id: Any,
        *,
        code: str,
        detail: str,
        extra: dict[str, Any] | None = None,
        status: str = "failed",
    ) -> None:
        """Record a delivery problem on a persisted outbound row.

        Mirrors the composer's `_mark_failed` in the worker, which this library
        cannot import (workers depend on ai_core, not the other way round). Uses
        its own session: the turn's session is already closed by this point.

        `status` is a parameter because a partial multi-bubble send is neither
        clean success nor clean failure — the row stays `sent` (the customer did
        read part of it) but still carries the error payload.
        """
        from sqlalchemy import update as _update

        from db import session_scope
        from db.models.conversation import Message as _Message

        payload: dict[str, Any] = {"code": code, "detail": detail}
        if extra:
            payload.update(extra)
        async with session_scope() as _s:
            await _s.execute(
                _update(_Message)
                .where(_Message.id == message_id)
                .values(status=status, failed_at=datetime.now(tz=UTC), error=payload)
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
        media: dict[str, Any] | None = None,
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
            conv = await convs.get_active_or_reopen_latest(
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

            echo_meta: dict[str, Any] | None = None
            if media and media.get("id"):
                echo_meta = {
                    "media": {
                        "kind": media.get("kind"),
                        "mime": media.get("mime"),
                        "wa_media_id": media.get("id"),
                        "caption": media.get("caption"),
                        "storage_path": None,
                    }
                }
            echo_msg = await msgs.persist_phone_echo_message(
                conversation_id=conv.id,
                merchant_id=resolved.merchant_id,
                content=text,
                wa_message_id=wa_message_id,
                meta=echo_meta,
            )
            # Download + store the attachment (best-effort) so a photo the
            # merchant sent from their handset shows in the inbox. No LLM turn —
            # the customer already received the message on WhatsApp.
            if echo_meta is not None and self._media_pipeline is not None:
                try:
                    patch = await self._media_pipeline.fetch_and_store(
                        api_key=resolved.api_key,
                        waba_base_url=resolved.waba_base_url,
                        phone_number_id=phone_number_id,
                        merchant_id=resolved.merchant_id,
                        conversation_id=conv.id,
                        message_id=echo_msg.id,
                        media_id=str(media["id"]),  # type: ignore[index]
                        kind=str((media or {}).get("kind") or ""),
                        mime=(media or {}).get("mime"),
                    )
                    if patch:
                        await msgs.patch_message_media(
                            wa_message_id=wa_message_id,
                            merchant_id=resolved.merchant_id,
                            media_patch=patch,
                        )
                except Exception as e:  # pragma: no cover - best effort
                    logger.warning(
                        "wa.phone_echo.media_failed", error=str(e), wa_message_id=wa_message_id
                    )
            await convs.touch_last_message(conv.id)
            # The merchant just answered from their phone: soft-pause the bot so
            # it doesn't talk over the human. Reset on every echo (each manual
            # reply extends the window); auto-resumes after. Quanto dura è una
            # scelta di presidio, non una costante: un negozio che risponde a
            # mano tutto il giorno la vuole lunga, uno che interviene di rado la
            # vuole corta.
            pause_minutes = await self._resolve_int(
                session,
                resolved.merchant_id,
                ConfigKey.HANDOFF_PHONE_ECHO_PAUSE_MINUTES,
                default=_PHONE_ECHO_PAUSE_FALLBACK_MIN,
            )
            conv.ai_disabled_until = datetime.now(UTC) + timedelta(minutes=pause_minutes)

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
        profile_id: UUID | None = None,
    ) -> str:
        """Resolve the system prompt for this turn (UC-09 aware).

        Delegates to `PromptManager`: when the conversation is enrolled in an
        A/B experiment and the assigned variant has an authored `system`
        template, that template's body is used — this is what makes the two
        arms behave differently (the persona/sentiment block below is
        deliberately bypassed for variant prompts, to keep experiments clean;
        playground corrections likewise apply only to the cascade fallback).
        Otherwise the config-cascade prompt below is used as the fallback.

        Profilo e A/B sono **ortogonali** (ADR 0022): se la conversazione ha un
        variant che fa swap del body, quel body vince e il profilo agisce solo
        su playbook/azioni/FSM; senza variant, il profilo modula la cascata
        normalmente attraverso il fallback qui sotto.
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
                profile_id=profile_id,
            ),
        )

    async def _cascade_system_prompt(
        self,
        *,
        session: Any,
        merchant_id: UUID,
        prior_sentiment: str | None = None,
        customer_message: str | None = None,
        profile_id: UUID | None = None,
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
            profile_id=profile_id,
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

    async def _resolve_handoff_prompt(
        self, session: Any, merchant_id: UUID, *, profile_id: UUID | None = None
    ) -> HandoffPrompt:
        """Istruzioni di handoff del merchant, per il prompt (ADR 0026).

        Degrada ai default a ogni errore: un problema di configurazione non deve
        togliere al bot la via d'uscita verso un operatore.
        """
        try:
            resolver = ConfigResolver(session)

            async def _get(key: ConfigKey) -> Any:
                return await resolver.resolve(key, merchant_id=merchant_id, profile_id=profile_id)

            enabled = await _get(ConfigKey.HANDOFF_ENABLED)
            mode = await _get(ConfigKey.HANDOFF_INSTRUCTIONS_MODE)
            criteria = await _get(ConfigKey.HANDOFF_INSTRUCTIONS_CRITERIA)
            exclusions = await _get(ConfigKey.HANDOFF_INSTRUCTIONS_EXCLUSIONS)
        except Exception:
            return HandoffPrompt()

        def _lines(raw: Any) -> tuple[str, ...]:
            if not isinstance(raw, list):
                return ()
            return tuple(str(x).strip() for x in raw if str(x).strip())

        return HandoffPrompt(
            enabled=enabled if isinstance(enabled, bool) else True,
            mode=mode if mode in ("extend", "replace") else "extend",
            criteria=_lines(criteria),
            exclusions=_lines(exclusions),
        )


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


_INTAKE_HIGH_INTENT = frozenset(
    [
        "prezzo",
        "costo",
        "acquistare",
        "comprare",
        "disponibile",
        "preventivo",
        "offerta",
        "sconto",
        "quando",
        "appuntamento",
        "informazioni",
        "interessato",
        "vorrei",
        "voglio",
        "book",
        "price",
        "buy",
        "available",
    ]
)


def _compute_intake_score(text: str) -> int:
    """Heuristic intent/intake score (0-100) for the first inbound message.

    Long messages and presence of high-intent keywords push the score up.
    This gives the scheduler and scoring a lightweight signal before any LLM call.
    """
    lower = text.lower()
    words = lower.split()
    keyword_hits = sum(1 for w in words if w.rstrip(".,!?") in _INTAKE_HIGH_INTENT)
    length_score = min(40, len(text) // 5)
    keyword_score = min(60, keyword_hits * 20)
    return min(100, length_score + keyword_score)


async def _assign_ab_variant(session: Any, *, merchant_id: UUID, lead_id: UUID) -> str | None:
    """Pick the oldest running experiment and assign a variant.

    S-06: When AB_THOMPSON_SAMPLING_ENABLED is True (default), uses Thompson
    Sampling to select the arm with the highest Beta-sampled conversion rate.
    Falls back to deterministic hash-pick if the bandit logic fails.
    """
    from ai_core.bandit.thompson import thompson_sample

    try:
        ab = ABRepository(session)
        running = await ab.list_active_for_merchant(merchant_id)
        if not running:
            return None

        experiment = running[0]
        variants = experiment.variants or []
        if not variants:
            return "default"

        # Check if Thompson Sampling is enabled for this merchant.
        try:
            resolver = ConfigResolver(session)
            use_ts = await resolver.resolve(
                ConfigKey.AB_THOMPSON_SAMPLING_ENABLED, merchant_id=merchant_id
            )
            thompson_enabled = bool(use_ts) if use_ts is not None else True
        except Exception:
            thompson_enabled = True

        if thompson_enabled:
            # Check for an existing assignment first (idempotency).
            from sqlalchemy import select as sa_select
            from db.models import ABAssignment

            existing_stmt = sa_select(ABAssignment).where(
                ABAssignment.experiment_id == experiment.id,
                ABAssignment.lead_id == lead_id,
            )
            existing = (await session.execute(existing_stmt)).scalar_one_or_none()
            if existing is not None:
                return existing.variant_id

            # Draw from Beta posteriors for each variant.
            wins, totals = await ab.get_variant_stats(
                experiment.id, primary_metric=experiment.primary_metric
            )
            variant_id = thompson_sample(variants, variant_wins=wins, variant_totals=totals)

            # Persist the assignment.
            session.add(
                ABAssignment(
                    experiment_id=experiment.id,
                    merchant_id=merchant_id,
                    lead_id=lead_id,
                    variant_id=variant_id,
                )
            )
            await session.flush()
            return variant_id

        return cast(
            "str | None",
            await ab.assign_variant(experiment, lead_id=lead_id, merchant_id=merchant_id),
        )
    except Exception as e:  # pragma: no cover — defensive only
        logger.warning("uc09.assignment_failed", error=str(e))
        return None
