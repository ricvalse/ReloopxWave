"""Config schema — the typed surface for the three-level cascade.

Keys map 1:1 to the table in section 9.4 of reloop-ai-architettura.md.
Adding a new configurable knob means: add a key here, add the default,
run the OpenAPI codegen to sync the frontend.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictModel(BaseModel):
    """Base for every config section: `extra='forbid'` so an unknown/typo'd key
    is rejected on write instead of being silently dropped (UC-10)."""

    model_config = ConfigDict(extra="forbid")


class ConfigKey(StrEnum):
    # UC-03 No answer — la cadenza dei follow-up vive INTERAMENTE sulla
    # lavagnetta (ADR 0014/0015): il ritardo sta in `delay_minutes` sul nodo
    # trigger, il contenuto in `free_text`/template sui nodi `send`. Le vecchie
    # chiavi `no_answer.*` (first/second_reminder_min, max_followups e i due
    # testi) sono state rimosse qui: erano rimaste esposte nel pannello merchant
    # senza che nessuna riga di codice le leggesse più.

    # UC-06 Reactivation
    REACTIVATION_DORMANT_DAYS = "reactivation.dormant_days"
    REACTIVATION_INTERVAL_DAYS = "reactivation.interval_days"
    REACTIVATION_MAX_ATTEMPTS = "reactivation.max_attempts"
    REACTIVATION_MESSAGE = "reactivation.message"

    # UC-04 Pipeline
    PIPELINE_ADVANCE_THRESHOLD = "pipeline.advance_threshold"
    PIPELINE_DEFAULT_PIPELINE_ID = "pipeline.default_pipeline_id"
    PIPELINE_NEW_STAGE_ID = "pipeline.new_stage_id"
    PIPELINE_QUALIFIED_STAGE_ID = "pipeline.qualified_stage_id"
    # When false, the deterministic move_pipeline injection (UC-04) is skipped —
    # the bot never auto-advances the lead in the CRM pipeline. Use-case agnostic
    # gate (a pure info/reminder bot has no sales pipeline). Default True = today.
    PIPELINE_AUTO_ADVANCE = "pipeline.auto_advance"

    # UC-05 Scoring
    SCORING_HOT_THRESHOLD = "scoring.hot_threshold"
    SCORING_COLD_THRESHOLD = "scoring.cold_threshold"
    # Master switch for the always-on cumulative lead scoring (UC-05). When false
    # no update_score is synthesized and the qualification context is dropped from
    # the prompt — for bots that don't qualify leads. Default True = today.
    SCORING_ENABLED = "scoring.enabled"

    # UC-09 A/B
    AB_DEFAULT_SPLIT = "ab_test.default_split"
    AB_MIN_SAMPLE = "ab_test.min_sample"
    AB_THOMPSON_SAMPLING_ENABLED = "ab_test.thompson_sampling_enabled"

    # Schedule
    SCHEDULE_ACTIVE_HOURS = "schedule.active_hours"
    SCHEDULE_OFF_HOURS_MESSAGE = "schedule.off_hours_message"
    SCHEDULE_TIMEZONE = "schedule.timezone"
    # Drop (don't auto-reply to) an inbound older than this many minutes. Guards
    # against the bot answering a stale backlog out of context after downtime.
    # 0 = disabled. The message is still persisted; only the reply is skipped.
    SCHEDULE_INBOUND_STALENESS_MIN = "schedule.inbound_staleness_min"

    # UC-07 RAG
    RAG_TOP_K = "rag.top_k"
    RAG_MIN_SCORE = "rag.min_score"
    RAG_HYDE_ENABLED = "rag.hyde_enabled"
    RAG_RERANK_ENABLED = "rag.rerank_enabled"
    RAG_RERANK_TOP_K = "rag.rerank_top_k"
    RAG_FRESHNESS_DECAY = "rag.freshness_decay"

    # Bot
    BOT_LANGUAGE = "bot.language"
    BOT_TONE = "bot.tone"

    # Escalation
    ESCALATION_ENABLED = "escalation.enabled"
    ESCALATION_HANDOFF_MESSAGE = "escalation.handoff_message"
    ESCALATION_SILENT_HANDOFF = "escalation.silent_handoff"
    # Keywords that force the escalation-model route (spec 6.7). None = use the
    # code default vocabulary (CRITICAL_KEYWORDS). A tenant whose domain reuses a
    # default word innocently (e.g. "concorrenza" in recruiting) can override it.
    ESCALATION_CRITICAL_KEYWORDS = "escalation.critical_keywords"
    # Minuti oltre i quali un handoff ancora aperto è "in ritardo": il cron
    # `handoff_sla_sweep` emette `conversation.handoff_overdue`, che è ciò che
    # fa scattare l'avviso all'operatore (es. il nodo Slack). Era una variabile
    # d'ambiente globale — stesso valore per ogni merchant della piattaforma —
    # e nessun merchant poteva adattarla ai propri orari di presidio.
    ESCALATION_SLA_MINUTES = "escalation.sla_minutes"
    # Minuti di silenzio del bot dopo che un umano ha scritto dall'app del
    # telefono (mirroring 360dialog Coexistence). Era hardcoded a 2 ore.
    ESCALATION_PHONE_ECHO_PAUSE_MINUTES = "escalation.phone_echo_pause_minutes"

    # Privacy
    PRIVACY_RETENTION_MONTHS = "privacy.retention_months"

    # UC-02 Booking
    BOOKING_DEFAULT_CALENDAR_ID = "booking.default_calendar_id"
    BOOKING_DEFAULT_DURATION_MIN = "booking.default_duration_min"
    BOOKING_LOOKAHEAD_DAYS = "booking.lookahead_days"
    # Lista di ore di anticipo per i promemoria WhatsApp dell'appuntamento.
    # Es.: [24] → un solo reminder 24h prima; [48, 24] → due reminder.
    BOOKING_REMINDER_SCHEDULE = "booking.reminder_schedule"
    # When false the bot never offers/handles appointments: the "Servizi
    # prenotabili" block is dropped from the prompt and booking actions are not
    # advertised. Use-case agnostic gate. Default True = today's behavior.
    BOOKING_ENABLED = "booking.enabled"

    # Lead capture — whether the bot proactively asks for the lead's identity
    # (name/email/need). Off = a pure info/reminder bot that never interviews.
    LEAD_CAPTURE_ENABLED = "lead_capture.enabled"

    # Business profile — fed into the system prompt so the bot knows who it
    # represents. Leaving any field empty is fine; the prompt builder simply
    # omits it.
    BUSINESS_NAME = "business.name"
    BUSINESS_INDUSTRY = "business.industry"
    BUSINESS_DESCRIPTION = "business.description"
    BUSINESS_OFFER = "business.offer"
    BUSINESS_HOURS = "business.hours"
    BUSINESS_LOCATION = "business.location"
    BUSINESS_PRICING_NOTES = "business.pricing_notes"
    BUSINESS_WEBSITE = "business.website"

    # Bot voice — prompt additions + first outbound message shown when the
    # merchant reaches out to a new lead.
    BOT_SYSTEM_PROMPT_ADDITIONS = "bot.system_prompt_additions"
    BOT_FIRST_MESSAGE = "bot.first_message"

    # Master kill switch for the bot. When false, the worker still persists
    # the inbound message and emits analytics, but skips the LLM turn entirely
    # — the merchant is expected to reply via the composer. Pairs with the
    # per-thread `conversations.auto_reply` flag (AND).
    BOT_AUTO_REPLY_ENABLED = "bot.auto_reply_enabled"

    # Bot persona — structured, guided knobs that drive the system prompt.
    # `formality` is the new primary tone-of-address driver (tu / Lei); when
    # "auto" the builder falls back to the freeform legacy `bot.tone` string.
    # The rest are orthogonal (length, emoji, greeting/signature, do/don't
    # lists, few-shot examples). `system_prompt_additions` stays the advanced
    # escape hatch.
    BOT_FORMALITY = "bot.formality"
    BOT_VERBOSITY = "bot.verbosity"
    BOT_EMOJI_POLICY = "bot.emoji_policy"
    BOT_GREETING_STYLE = "bot.greeting_style"
    BOT_SIGNATURE = "bot.signature"
    # Nome con cui l'assistente si presenta. Alimenta il blocco anti-drift in
    # coda al prompt: senza un nome da difendere il bot adotta quello che trova
    # nei messaggi scritti a mano dall'operatore. None = non dichiara un nome.
    BOT_ASSISTANT_NAME = "bot.assistant_name"
    BOT_DO_PHRASES = "bot.do_phrases"
    BOT_DONT_PHRASES = "bot.dont_phrases"
    BOT_EXAMPLES = "bot.examples"
    # When true, the prior turn's lead.sentiment injects an empathy/upsell hint
    # into the prompt. Uses the previous turn's value (zero added latency).
    BOT_SENTIMENT_ADAPTATION_ENABLED = "bot.sentiment_adaptation_enabled"

    # Delivery realism — make the WhatsApp reply feel human. All default to a
    # no-op (today's behavior): instant single send, no typing indicator, no
    # debounce. Each is per-merchant opt-in via the cascade.
    DELIVERY_DEBOUNCE_WINDOW_S = "delivery.debounce_window_s"
    DELIVERY_TYPING_INDICATOR_ENABLED = "delivery.typing_indicator_enabled"
    DELIVERY_TYPING_DELAY_BASE_S = "delivery.typing_delay_base_s"
    DELIVERY_TYPING_DELAY_PER_CHAR_S = "delivery.typing_delay_per_char_s"
    DELIVERY_TYPING_DELAY_MIN_S = "delivery.typing_delay_min_s"
    DELIVERY_TYPING_DELAY_MAX_S = "delivery.typing_delay_max_s"
    DELIVERY_TYPING_JITTER_FRAC = "delivery.typing_jitter_frac"
    DELIVERY_MULTI_BUBBLE_MAX = "delivery.multi_bubble_max"
    DELIVERY_BUBBLE_MAX_CHARS = "delivery.bubble_max_chars"

    # Agent reasoning loop — Amalia-style tool-use. When enabled the orchestrator
    # may call read-only tools (live calendar availability, upcoming appointments)
    # mid-turn, see the real result, and adapt its reply before sending — so it
    # never promises an unavailable slot. `max_tool_iterations` caps the LLM calls
    # per turn (1 = single-shot, no tool grounding).
    AGENT_TOOL_USE_ENABLED = "agent.tool_use_enabled"
    AGENT_MAX_TOOL_ITERATIONS = "agent.max_tool_iterations"
    AGENT_COHERENCE_GUARD_ENABLED = "agent.coherence_guard_enabled"
    AGENT_CONTEXT_COMPRESS_THRESHOLD = "agent.context_compress_threshold"

    # GHL CRM sync (contratto capitolato sez.5) — map our collected lead fields
    # to the merchant's GHL custom-field ids, and tag every synced contact.
    # `field_map` is a {our_field_name -> GHL custom field id} dict; the contact
    # upsert turns each present value into a `customFields` entry. `default_tags`
    # is applied to every upserted contact.
    GHL_CONTACT_FIELD_MAP = "ghl.contact_field_map"
    GHL_CONTACT_DEFAULT_TAGS = "ghl.contact_default_tags"

    # UC-13 Objections — the category vocabulary the classifier maps to.
    OBJECTION_CATEGORIES = "objections.categories"

    # Conversation lifecycle — idle close drives the objection-extraction sweep.
    CONVERSATION_IDLE_CLOSE_MINUTES = "conversation.idle_close_minutes"
    # Conversation playbook (ADR 0018) — the per-tenant, use-case-agnostic knobs
    # that govern the conversation's SHAPE. Resolved PER-LEAF through the cascade
    # (like every other key) so a merchant can override the mode while inheriting
    # the agency's directives. Default = today's sales FSM (mode fsm_legacy, no
    # directives, all actions allowed).
    CONVERSATION_PLAYBOOK_MODE = "conversation.playbook.mode"
    CONVERSATION_PLAYBOOK_GOAL = "conversation.playbook.goal"
    CONVERSATION_PLAYBOOK_DIRECTIVES = "conversation.playbook.directives"
    CONVERSATION_PLAYBOOK_ACTIONS_ENABLED = "conversation.playbook.actions.enabled"

    # Dashboard configurabile (ADR 0021) — QUALI metriche event-based mostra la
    # dashboard di questo merchant. Risolta come doc atomico attraverso la
    # cascata: il merchant SOSTITUISCE la lista d'agenzia (replace per-leaf, non
    # merge). `event_type` deve appartenere al catalogo `db.analytics_events`.
    DASHBOARD_METRICS = "dashboard.metrics"


# Dashboard di default (ADR 0021): riproduce le metriche che la dashboard
# mostrava hardcoded, così un merchant senza override vede esattamente quello di
# prima. `id` è la chiave stabile usata dal FE; `event_type` deve appartenere al
# catalogo (validato da `MetricDefinitionSchema`).
_DEFAULT_DASHBOARD_METRICS: list[dict[str, Any]] = [
    {
        "id": "bookings_created",
        "label": "Appuntamenti presi",
        "source": "event",
        "event_type": "booking.created",
    },
    {
        "id": "messages_received",
        "label": "Messaggi ricevuti",
        "source": "event",
        "event_type": "message.received",
    },
    {
        "id": "messages_replied",
        "label": "Risposte inviate",
        "source": "event",
        "event_type": "message.replied",
    },
    {
        "id": "pipeline_moved",
        "label": "Spostamenti in pipeline",
        "source": "event",
        "event_type": "pipeline.moved",
    },
    {
        "id": "reminders_sent",
        "label": "Promemoria inviati",
        "source": "event",
        "event_type": "appointment_reminder.sent",
    },
]

# Bolle strutturali offerte in aggiunta al catalogo eventi. Non sono nei default
# (che riproducono la dashboard storica) ma il metric-builder le propone come
# "automatiche": non richiedono né una dichiarazione né un'automazione.
STRUCTURAL_METRIC_PRESETS: list[dict[str, Any]] = [
    {
        "id": "automation_messages_sent",
        "label": "Messaggi inviati",
        "source": "messages",
        "direction": "out",
        "sender_types": ["automation", "automation_ai"],
    },
    {
        "id": "automation_replies_received",
        "label": "Risposte ricevute",
        "source": "messages",
        "direction": "out",
        "sender_types": ["automation", "automation_ai"],
        "has_reply": True,
    },
    {
        "id": "automation_people_reached",
        "label": "Persone raggiunte",
        "source": "messages",
        "direction": "out",
        "sender_types": ["automation", "automation_ai"],
        "aggregation": "count_unique",
    },
]


# Default objection vocabulary (UC-13); merchants can override per-tenant.
_DEFAULT_OBJECTION_CATEGORIES = [
    "prezzo",
    "fiducia",
    "tempistiche",
    "concorrenza",
    "necessita",
    "altro",
]


SYSTEM_DEFAULTS: dict[ConfigKey, Any] = {
    ConfigKey.REACTIVATION_DORMANT_DAYS: 90,
    ConfigKey.REACTIVATION_INTERVAL_DAYS: 7,
    ConfigKey.REACTIVATION_MAX_ATTEMPTS: 3,
    ConfigKey.REACTIVATION_MESSAGE: None,
    ConfigKey.PIPELINE_ADVANCE_THRESHOLD: 60,
    ConfigKey.PIPELINE_DEFAULT_PIPELINE_ID: None,
    ConfigKey.PIPELINE_NEW_STAGE_ID: None,
    ConfigKey.PIPELINE_QUALIFIED_STAGE_ID: None,
    ConfigKey.PIPELINE_AUTO_ADVANCE: True,
    ConfigKey.SCORING_HOT_THRESHOLD: 80,
    ConfigKey.SCORING_COLD_THRESHOLD: 30,
    ConfigKey.SCORING_ENABLED: True,
    ConfigKey.AB_DEFAULT_SPLIT: [50, 50],
    ConfigKey.AB_MIN_SAMPLE: 100,
    ConfigKey.SCHEDULE_ACTIVE_HOURS: "24/7",
    ConfigKey.SCHEDULE_OFF_HOURS_MESSAGE: "Grazie per averci contattato! Ti risponderemo al più presto.",
    ConfigKey.SCHEDULE_TIMEZONE: "Europe/Rome",
    ConfigKey.SCHEDULE_INBOUND_STALENESS_MIN: 10,
    ConfigKey.RAG_TOP_K: 5,
    # 0.7 era irraggiungibile dalla query grezza su text-embedding-3-small
    # (~0.66) → il retrieval tornava sempre vuoto. Il resolver legge QUESTO
    # dict (non il Field Pydantic di BotConfigSchema), quindi il valore va
    # abbassato qui perché il fix abbia effetto a runtime.
    ConfigKey.RAG_MIN_SCORE: 0.6,
    ConfigKey.RAG_HYDE_ENABLED: True,
    ConfigKey.RAG_RERANK_ENABLED: True,
    ConfigKey.RAG_RERANK_TOP_K: 5,
    ConfigKey.RAG_FRESHNESS_DECAY: 0.01,
    ConfigKey.BOT_LANGUAGE: "it",
    ConfigKey.BOT_TONE: "professionale-amichevole",
    ConfigKey.ESCALATION_ENABLED: True,
    ConfigKey.ESCALATION_HANDOFF_MESSAGE: None,
    ConfigKey.ESCALATION_SILENT_HANDOFF: False,
    ConfigKey.ESCALATION_CRITICAL_KEYWORDS: None,
    ConfigKey.ESCALATION_SLA_MINUTES: 15,
    ConfigKey.ESCALATION_PHONE_ECHO_PAUSE_MINUTES: 120,
    ConfigKey.PRIVACY_RETENTION_MONTHS: 24,
    ConfigKey.BOOKING_DEFAULT_CALENDAR_ID: None,
    ConfigKey.BOOKING_DEFAULT_DURATION_MIN: 30,
    ConfigKey.BOOKING_LOOKAHEAD_DAYS: 14,
    ConfigKey.BOOKING_REMINDER_SCHEDULE: [24],
    ConfigKey.BOOKING_ENABLED: True,
    ConfigKey.LEAD_CAPTURE_ENABLED: True,
    ConfigKey.BUSINESS_NAME: None,
    ConfigKey.BUSINESS_INDUSTRY: None,
    ConfigKey.BUSINESS_DESCRIPTION: None,
    ConfigKey.BUSINESS_OFFER: None,
    ConfigKey.BUSINESS_HOURS: None,
    ConfigKey.BUSINESS_LOCATION: None,
    ConfigKey.BUSINESS_PRICING_NOTES: None,
    ConfigKey.BUSINESS_WEBSITE: None,
    ConfigKey.BOT_SYSTEM_PROMPT_ADDITIONS: None,
    ConfigKey.BOT_FIRST_MESSAGE: None,
    ConfigKey.BOT_AUTO_REPLY_ENABLED: False,
    # Persona — sensible "on" defaults (mild prompt enrichment for everyone).
    ConfigKey.BOT_FORMALITY: "auto",
    ConfigKey.BOT_VERBOSITY: "equilibrato",
    ConfigKey.BOT_EMOJI_POLICY: "sobrio",
    ConfigKey.BOT_GREETING_STYLE: None,
    ConfigKey.BOT_SIGNATURE: None,
    ConfigKey.BOT_ASSISTANT_NAME: None,
    ConfigKey.BOT_DO_PHRASES: [],
    ConfigKey.BOT_DONT_PHRASES: [],
    ConfigKey.BOT_EXAMPLES: [],
    ConfigKey.BOT_SENTIMENT_ADAPTATION_ENABLED: True,
    # Delivery — "human-feel" defaults (ADR 0008/0010): coalesce rapid messages,
    # show a typing indicator, pause briefly before sending, and split long
    # replies into a couple of bubbles. Merchants can dial any of these back to 0
    # via the cascade to restore the old instant single-send behavior.
    ConfigKey.DELIVERY_DEBOUNCE_WINDOW_S: 8,
    ConfigKey.DELIVERY_TYPING_INDICATOR_ENABLED: True,
    ConfigKey.DELIVERY_TYPING_DELAY_BASE_S: 1.0,
    ConfigKey.DELIVERY_TYPING_DELAY_PER_CHAR_S: 0.02,
    ConfigKey.DELIVERY_TYPING_DELAY_MIN_S: 1.0,
    ConfigKey.DELIVERY_TYPING_DELAY_MAX_S: 6.0,
    ConfigKey.DELIVERY_TYPING_JITTER_FRAC: 0.25,
    ConfigKey.DELIVERY_MULTI_BUBBLE_MAX: 2,
    ConfigKey.DELIVERY_BUBBLE_MAX_CHARS: 600,
    # Agent tool-use loop — on by default; up to 3 LLM calls per turn so the
    # model can ground itself on live data (availability/appointments) once or
    # twice before replying.
    ConfigKey.AGENT_TOOL_USE_ENABLED: True,
    ConfigKey.AGENT_MAX_TOOL_ITERATIONS: 3,
    ConfigKey.AGENT_COHERENCE_GUARD_ENABLED: True,
    ConfigKey.AGENT_CONTEXT_COMPRESS_THRESHOLD: 30,
    ConfigKey.AB_THOMPSON_SAMPLING_ENABLED: True,
    ConfigKey.GHL_CONTACT_FIELD_MAP: {},
    ConfigKey.GHL_CONTACT_DEFAULT_TAGS: [],
    ConfigKey.OBJECTION_CATEGORIES: _DEFAULT_OBJECTION_CATEGORIES,
    ConfigKey.CONVERSATION_IDLE_CLOSE_MINUTES: 120,
    ConfigKey.CONVERSATION_PLAYBOOK_MODE: "fsm_legacy",
    ConfigKey.CONVERSATION_PLAYBOOK_GOAL: None,
    ConfigKey.CONVERSATION_PLAYBOOK_DIRECTIVES: [],
    ConfigKey.CONVERSATION_PLAYBOOK_ACTIONS_ENABLED: None,
    ConfigKey.DASHBOARD_METRICS: _DEFAULT_DASHBOARD_METRICS,
}


class BotConfigSchema(_StrictModel):
    """Typed view over the JSONB override bag — validated at write time."""

    reactivation: ReactivationConfig = Field(default_factory=lambda: ReactivationConfig())
    pipeline: PipelineConfig = Field(default_factory=lambda: PipelineConfig())
    scoring: ScoringConfig = Field(default_factory=lambda: ScoringConfig())
    ab_test: ABTestConfig = Field(default_factory=lambda: ABTestConfig())
    schedule: ScheduleConfig = Field(default_factory=lambda: ScheduleConfig())
    rag: RagConfig = Field(default_factory=lambda: RagConfig())
    bot: BotSurfaceConfig = Field(default_factory=lambda: BotSurfaceConfig())
    escalation: EscalationConfig = Field(default_factory=lambda: EscalationConfig())
    privacy: PrivacyConfig = Field(default_factory=lambda: PrivacyConfig())
    booking: BookingConfig = Field(default_factory=lambda: BookingConfig())
    lead_capture: LeadCaptureConfig = Field(default_factory=lambda: LeadCaptureConfig())
    business: BusinessConfig = Field(default_factory=lambda: BusinessConfig())
    delivery: DeliveryConfig = Field(default_factory=lambda: DeliveryConfig())
    agent: AgentConfig = Field(default_factory=lambda: AgentConfig())
    objections: ObjectionsConfig = Field(default_factory=lambda: ObjectionsConfig())
    conversation: ConversationConfig = Field(default_factory=lambda: ConversationConfig())
    ghl: GHLConfig = Field(default_factory=lambda: GHLConfig())
    dashboard: DashboardConfig = Field(default_factory=lambda: DashboardConfig())


class ReactivationConfig(_StrictModel):
    dormant_days: int = Field(90, ge=30, le=180)
    interval_days: int = Field(7, ge=3, le=30)
    max_attempts: int = Field(3, ge=1, le=5)
    # Single message used for every reactivation attempt when set; None falls
    # back to the worker's built-in per-attempt copy.
    message: str | None = Field(default=None, max_length=1000)


class PipelineConfig(_StrictModel):
    advance_threshold: int = Field(60, ge=0, le=100)
    default_pipeline_id: str | None = None
    new_stage_id: str | None = None
    qualified_stage_id: str | None = None
    # Deterministic pipeline advancement (UC-04). False = never auto-advance.
    auto_advance: bool = True


class ScoringConfig(_StrictModel):
    hot_threshold: int = Field(80, ge=50, le=100)
    cold_threshold: int = Field(30, ge=0, le=50)
    # Always-on cumulative lead scoring (UC-05). False disables it entirely.
    enabled: bool = True


class LeadCaptureConfig(_StrictModel):
    """Whether the bot proactively collects the lead's identity (name/email/
    need). Off = a pure info/reminder bot that never interviews the contact."""

    enabled: bool = True


class ABTestConfig(_StrictModel):
    default_split: list[int] = Field(default_factory=lambda: [50, 50])
    min_sample: int = Field(100, ge=50, le=1000)
    thompson_sampling_enabled: bool = True


class ScheduleConfig(_StrictModel):
    active_hours: str = "24/7"
    off_hours_message: str = "Grazie per averci contattato! Ti risponderemo al più presto."
    timezone: str = "Europe/Rome"
    # Skip auto-replying to an inbound older than this many minutes (still
    # persisted). 0 = disabled. Defends against answering a stale backlog
    # out of context after the worker was down. Up to 24h.
    inbound_staleness_min: int = Field(10, ge=0, le=1440)


class RagConfig(_StrictModel):
    top_k: int = Field(5, ge=3, le=10)
    # 0.7 era irraggiungibile dalla query grezza su text-embedding-3-small
    # (una domanda quasi-verbatim scora ~0.66); con HyDE attivo lo score sale
    # ~0.85, ma 0.6 tiene comunque un margine ampio sull'off-topic (~0.17).
    min_score: float = Field(0.6, ge=0.4, le=0.9)
    hyde_enabled: bool = True
    rerank_enabled: bool = True
    rerank_top_k: int = Field(5, ge=1, le=20)
    freshness_decay: float = Field(0.01, ge=0.0, le=0.5)


class BotExample(BaseModel):
    """One few-shot style example. Guides the bot's voice, not its facts."""

    q: str = Field(max_length=300)
    a: str = Field(max_length=600)


class BotSurfaceConfig(_StrictModel):
    language: str = "it"
    # Legacy freeform tone. Kept as the fallback when `register == "auto"` so
    # merchants who customized it keep today's behavior verbatim.
    tone: str = "professionale-amichevole"
    # Structured persona knobs (the guided UI). `auto` defers to `tone`.
    formality: Literal["dai-del-tu", "dai-del-lei", "auto"] = "auto"
    verbosity: Literal["conciso", "equilibrato", "dettagliato"] = "equilibrato"
    emoji_policy: Literal["mai", "sobrio", "libero"] = "sobrio"
    greeting_style: str | None = Field(default=None, max_length=200)
    signature: str | None = Field(default=None, max_length=200)
    assistant_name: str | None = Field(default=None, max_length=80)
    do_phrases: list[str] = Field(default_factory=list, max_length=10)
    dont_phrases: list[str] = Field(default_factory=list, max_length=10)
    examples: list[BotExample] = Field(default_factory=list, max_length=5)
    sentiment_adaptation_enabled: bool = True
    system_prompt_additions: str | None = Field(default=None, max_length=4000)
    first_message: str | None = Field(default=None, max_length=1000)
    # Master kill switch for auto-reply. AND-ed with `conversations.auto_reply`
    # at the worker. False = bot stays silent, agent must reply via composer.
    auto_reply_enabled: bool = False


class BusinessConfig(_StrictModel):
    """Merchant-facing profile — names, offer, hours. All optional. Fed into
    the orchestrator's system prompt so the bot speaks for this merchant.
    """

    name: str | None = Field(default=None, max_length=120)
    industry: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1500)
    offer: str | None = Field(default=None, max_length=1500)
    hours: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=500)
    pricing_notes: str | None = Field(default=None, max_length=1500)
    website: str | None = Field(default=None, max_length=300)


class EscalationConfig(_StrictModel):
    enabled: bool = True
    # Fixed message sent to the customer on handoff. None → keep the LLM's line.
    handoff_message: str | None = Field(default=None, max_length=1000)
    # When true, hand off silently (no customer-facing message at all).
    silent_handoff: bool = False
    # Keywords that force the escalation model route. None = code default
    # vocabulary (CRITICAL_KEYWORDS). [] = disable keyword-forced escalation.
    critical_keywords: list[str] | None = Field(default=None, max_length=40)
    # Soglia di "handoff in ritardo". Il minimo di 1 minuto è deliberato: sotto
    # lo zero il cutoff finirebbe nel futuro (`now - -5min`) e ogni handoff
    # aperto risulterebbe scaduto — la stessa guardia che lo sweep applicava a
    # mano quando il valore arrivava dall'ambiente.
    sla_minutes: int = Field(15, ge=1, le=1440)
    # Quanto il bot resta zitto dopo un messaggio scritto a mano dal telefono.
    phone_echo_pause_minutes: int = Field(120, ge=5, le=10080)


class PrivacyConfig(_StrictModel):
    retention_months: int = Field(24, ge=6, le=60)


class BookingConfig(_StrictModel):
    default_calendar_id: str | None = None
    default_duration_min: int = Field(30, ge=15, le=240)
    lookahead_days: int = Field(14, ge=1, le=60)
    # Ore di anticipo per ogni promemoria WhatsApp. Es.: [48, 24] → due reminder.
    # Max 5 voci; valori in ore (1-168). Duplicati vengono ignorati.
    reminder_schedule: list[int] = Field(default_factory=lambda: [24], max_length=5)
    # Whether the bot offers/handles appointments at all. False drops the
    # "Servizi prenotabili" block and hides booking actions from the model.
    enabled: bool = True


class ObjectionsConfig(_StrictModel):
    # The category vocabulary the objection classifier maps to (UC-13).
    categories: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_OBJECTION_CATEGORIES), max_length=30
    )


class PlaybookActionsConfig(_StrictModel):
    """Action allowlist for the conversation. `enabled` lists the orchestrator
    action kinds the AI may emit this conversation; None = all actions allowed
    (today's behavior). Values are `ActionKind` strings (e.g. "book_slot",
    "escalate_human", "none")."""

    enabled: list[str] | None = Field(default=None, max_length=12)


class PlaybookConfig(_StrictModel):
    """Conversation playbook (ADR 0018) — the use-case-agnostic doc that governs
    the conversation's shape. Default = today's sales FSM behavior.

    - `mode`: "fsm_legacy" (default) runs the built-in sales FSM hints;
      "off" runs NO per-turn state hint (pure directive-driven bot);
      "data" (Fase 1, not yet consumed by the engine) will drive a data-defined
      state machine — treated as fsm_legacy until then.
    - `goal`: optional north-star, folded into the directives block.
    - `directives`: authoritative behavioral rules, injected high-salience.
    - `actions`: the action allowlist.
    """

    mode: Literal["fsm_legacy", "off", "data"] = "fsm_legacy"
    goal: str | None = Field(default=None, max_length=2000)
    directives: list[str] = Field(default_factory=list, max_length=25)
    actions: PlaybookActionsConfig = Field(default_factory=lambda: PlaybookActionsConfig())


class ConversationConfig(_StrictModel):
    # Minutes of inactivity after which a conversation is auto-closed and its
    # objections extracted (UC-13 sweep).
    idle_close_minutes: int = Field(120, ge=15, le=10080)
    # Conversation playbook (ADR 0018) — resolved as one atomic doc.
    playbook: PlaybookConfig = Field(default_factory=lambda: PlaybookConfig())


class MetricDefinitionSchema(_StrictModel):
    """Una bolla della pagina Statistiche configurabile (ADR 0021 + 0047).

    Le bolle hanno **tre sorgenti**, e la distinzione non è cosmetica:

    - `messages` — strutturale. Il vocabolario sta nel codice (`direction`,
      `sender_type`, `automation_id` esistono per ogni merchant), quindi la
      bolla funziona senza configurare nulla e senza cablarla in un'automazione.
      Copre "messaggi inviati" e "risposte ricevute": lo stesso insieme letto due
      volte, il che è ciò che rende sensato il rapporto fra i due numeri.
    - `outcome` — **custom**. Il vocabolario appartiene al merchant, va prima
      dichiarato (`outcome_definitions`) e poi cablato in un nodo `emit_outcome`.
      È il caso "ha compilato il questionario".
    - `event` — il catalogo eventi tipato preesistente (booking, pipeline,
      escalation…).

    In tutti e tre i casi il riferimento è validato contro un vocabolario, mai
    una stringa libera: è la lezione del bug `reminder.sent` (una KPI ferma a
    zero per mesi perché emettitore e lettore usavano stringhe diverse).
    """

    # Chiave stabile usata dal FE (e per riconoscere una metrica fra i salvataggi).
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=80)
    source: Literal["event", "messages", "outcome"] = "event"

    # --- source="event" ---------------------------------------------------
    event_type: str | None = Field(default=None, max_length=64)

    # --- source="outcome" -------------------------------------------------
    # UUID di una `outcome_definitions`. Non una key testuale: una FK non si può
    # sbagliare a digitare.
    outcome_id: str | None = Field(default=None, max_length=64)

    # --- source="messages" ------------------------------------------------
    direction: Literal["in", "out"] | None = None
    sender_types: list[str] = Field(default_factory=list, max_length=8)
    # True → solo gli invii che hanno ottenuto risposta; False → solo quelli
    # rimasti senza; None → nessun filtro. È così che "risposte ricevute" è un
    # sottoinsieme di "messaggi inviati" invece di una misura scorrelata.
    has_reply: bool | None = None
    automation_node_key: str | None = Field(default=None, max_length=64)

    # --- comuni -----------------------------------------------------------
    # None = usa la finestra globale della dashboard (il `since_days` della query).
    window_days: int | None = Field(default=None, ge=1, le=365)
    # `count` conta le righe; `count_unique` conta i soggetti distinti
    # (conversazioni per `messages`). Serve per "quante persone" invece di
    # "quanti messaggi". Per `outcome` con cardinalità `once_per_lead` i due
    # coincidono già grazie all'indice unique.
    aggregation: Literal["count", "count_unique"] = "count"

    @field_validator("event_type")
    @classmethod
    def _event_type_must_be_known(cls, v: str | None) -> str | None:
        if v is None:
            return None
        # Import locale: tiene il modulo importabile anche se `db` non è
        # installato (es. tooling che carica solo lo schema).
        from db.analytics_events import EventType

        valid = {e.value for e in EventType}
        if v not in valid:
            raise ValueError(
                f"event_type sconosciuto: {v!r}. Deve appartenere al catalogo eventi "
                f"(GET /analytics/event-catalog)."
            )
        return v

    @model_validator(mode="after")
    def _source_requires_its_reference(self) -> MetricDefinitionSchema:
        """Ogni sorgente deve portare il proprio riferimento.

        Senza questo, una bolla `outcome` senza `outcome_id` verrebbe salvata e
        poi mostrerebbe zero per sempre — di nuovo il fallimento silenzioso che
        il catalogo tipato doveva chiudere.
        """
        if self.source == "event" and not self.event_type:
            raise ValueError("una metrica source='event' richiede event_type")
        if self.source == "outcome" and not self.outcome_id:
            raise ValueError("una metrica source='outcome' richiede outcome_id")
        if self.source == "messages" and self.direction is None:
            raise ValueError("una metrica source='messages' richiede direction ('in' o 'out')")
        return self


class DashboardConfig(_StrictModel):
    """Quali metriche mostra la dashboard di questo merchant (ADR 0021).

    Risolta come doc atomico dalla cascata: una lista impostata a livello
    merchant SOSTITUISCE quella d'agenzia (replace per-leaf, non merge).
    """

    metrics: list[MetricDefinitionSchema] = Field(
        default_factory=lambda: [MetricDefinitionSchema(**m) for m in _DEFAULT_DASHBOARD_METRICS],
        max_length=24,
    )

    @field_validator("metrics")
    @classmethod
    def _ids_must_be_unique(cls, v: list[MetricDefinitionSchema]) -> list[MetricDefinitionSchema]:
        ids = [m.id for m in v]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"id metrica duplicati: {', '.join(dupes)}")
        return v


class GHLConfig(_StrictModel):
    """GHL CRM sync knobs (contratto capitolato sez.5). `contact_field_map`
    maps our collected lead field names to the merchant's GHL custom-field ids
    so the contact upsert writes `customFields`; `contact_default_tags` tags
    every synced contact."""

    # {our_field_name -> GHL custom field id}. e.g. {"budget": "abc123"}.
    contact_field_map: dict[str, str] = Field(default_factory=dict)
    contact_default_tags: list[str] = Field(default_factory=list, max_length=20)


class DeliveryConfig(_StrictModel):
    """Human-feel delivery knobs. Defaults make the reply feel human out of the
    box (coalesce rapid messages, typing indicator, brief pause, a couple of
    bubbles); set any of them to 0/False to restore instant single-send."""

    # Quiet-period seconds: coalesce rapid inbound messages into one reply.
    # 0 = off (reply synchronously).
    debounce_window_s: int = Field(8, ge=0, le=30)
    # Send a WhatsApp read receipt + "typing…" indicator before replying.
    typing_indicator_enabled: bool = True
    # Artificial "thinking/typing" pause before sending, as base + per-char,
    # clamped to [min, max] with +/- jitter. max=0 disables the pause.
    typing_delay_base_s: float = Field(1.0, ge=0.0, le=10.0)
    typing_delay_per_char_s: float = Field(0.02, ge=0.0, le=0.2)
    typing_delay_min_s: float = Field(1.0, ge=0.0, le=20.0)
    typing_delay_max_s: float = Field(6.0, ge=0.0, le=20.0)
    typing_jitter_frac: float = Field(0.25, ge=0.0, le=1.0)
    # Split a long reply into up to N WhatsApp bubbles. 1 = single send.
    multi_bubble_max: int = Field(2, ge=1, le=4)
    # Low values are a legitimate "one sentence per bubble" mode: the splitter
    # never cuts mid-word, so a sentence longer than this stays whole.
    bubble_max_chars: int = Field(600, ge=20, le=1000)


class AgentConfig(_StrictModel):
    """Agent reasoning loop (Amalia-style tool-use). When enabled the
    orchestrator can call read-only tools mid-turn (live availability, upcoming
    appointments) and adapt its reply to the real result before sending."""

    tool_use_enabled: bool = True
    # Total LLM calls allowed per turn. 1 = single-shot (no tool grounding);
    # 3 allows up to two tool round-trips.
    max_tool_iterations: int = Field(3, ge=1, le=5)
    coherence_guard_enabled: bool = True
    context_compress_threshold: int = Field(30, ge=1, le=200)
