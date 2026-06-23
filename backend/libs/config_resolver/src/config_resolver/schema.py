"""Config schema — the typed surface for the three-level cascade.

Keys map 1:1 to the table in section 9.4 of reloop-ai-architettura.md.
Adding a new configurable knob means: add a key here, add the default,
run the OpenAPI codegen to sync the frontend.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    """Base for every config section: `extra='forbid'` so an unknown/typo'd key
    is rejected on write instead of being silently dropped (UC-10)."""

    model_config = ConfigDict(extra="forbid")


class ConfigKey(StrEnum):
    # UC-03 No answer
    NO_ANSWER_FIRST_REMINDER_MIN = "no_answer.first_reminder_min"
    NO_ANSWER_SECOND_REMINDER_MIN = "no_answer.second_reminder_min"
    NO_ANSWER_MAX_FOLLOWUPS = "no_answer.max_followups"
    NO_ANSWER_FIRST_REMINDER_TEXT = "no_answer.first_reminder_text"
    NO_ANSWER_SECOND_REMINDER_TEXT = "no_answer.second_reminder_text"

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

    # UC-05 Scoring
    SCORING_HOT_THRESHOLD = "scoring.hot_threshold"
    SCORING_COLD_THRESHOLD = "scoring.cold_threshold"

    # UC-09 A/B
    AB_DEFAULT_SPLIT = "ab_test.default_split"
    AB_MIN_SAMPLE = "ab_test.min_sample"

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

    # Bot
    BOT_LANGUAGE = "bot.language"
    BOT_TONE = "bot.tone"

    # Escalation
    ESCALATION_ENABLED = "escalation.enabled"
    ESCALATION_HANDOFF_MESSAGE = "escalation.handoff_message"
    ESCALATION_SILENT_HANDOFF = "escalation.silent_handoff"

    # Privacy
    PRIVACY_RETENTION_MONTHS = "privacy.retention_months"

    # UC-02 Booking
    BOOKING_DEFAULT_CALENDAR_ID = "booking.default_calendar_id"
    BOOKING_DEFAULT_DURATION_MIN = "booking.default_duration_min"
    BOOKING_LOOKAHEAD_DAYS = "booking.lookahead_days"

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

    # UC-13 Objections — the category vocabulary the classifier maps to.
    OBJECTION_CATEGORIES = "objections.categories"

    # Conversation lifecycle — idle close drives the objection-extraction sweep.
    CONVERSATION_IDLE_CLOSE_MINUTES = "conversation.idle_close_minutes"


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
    ConfigKey.NO_ANSWER_FIRST_REMINDER_MIN: 120,
    ConfigKey.NO_ANSWER_SECOND_REMINDER_MIN: 1440,
    ConfigKey.NO_ANSWER_MAX_FOLLOWUPS: 2,
    ConfigKey.NO_ANSWER_FIRST_REMINDER_TEXT: None,
    ConfigKey.NO_ANSWER_SECOND_REMINDER_TEXT: None,
    ConfigKey.REACTIVATION_DORMANT_DAYS: 90,
    ConfigKey.REACTIVATION_INTERVAL_DAYS: 7,
    ConfigKey.REACTIVATION_MAX_ATTEMPTS: 3,
    ConfigKey.REACTIVATION_MESSAGE: None,
    ConfigKey.PIPELINE_ADVANCE_THRESHOLD: 60,
    ConfigKey.PIPELINE_DEFAULT_PIPELINE_ID: None,
    ConfigKey.PIPELINE_NEW_STAGE_ID: None,
    ConfigKey.PIPELINE_QUALIFIED_STAGE_ID: None,
    ConfigKey.SCORING_HOT_THRESHOLD: 80,
    ConfigKey.SCORING_COLD_THRESHOLD: 30,
    ConfigKey.AB_DEFAULT_SPLIT: [50, 50],
    ConfigKey.AB_MIN_SAMPLE: 100,
    ConfigKey.SCHEDULE_ACTIVE_HOURS: "24/7",
    ConfigKey.SCHEDULE_OFF_HOURS_MESSAGE: "Grazie per averci contattato! Ti risponderemo al più presto.",
    ConfigKey.SCHEDULE_TIMEZONE: "Europe/Rome",
    ConfigKey.SCHEDULE_INBOUND_STALENESS_MIN: 10,
    ConfigKey.RAG_TOP_K: 5,
    ConfigKey.RAG_MIN_SCORE: 0.7,
    ConfigKey.BOT_LANGUAGE: "it",
    ConfigKey.BOT_TONE: "professionale-amichevole",
    ConfigKey.ESCALATION_ENABLED: True,
    ConfigKey.ESCALATION_HANDOFF_MESSAGE: None,
    ConfigKey.ESCALATION_SILENT_HANDOFF: False,
    ConfigKey.PRIVACY_RETENTION_MONTHS: 24,
    ConfigKey.BOOKING_DEFAULT_CALENDAR_ID: None,
    ConfigKey.BOOKING_DEFAULT_DURATION_MIN: 30,
    ConfigKey.BOOKING_LOOKAHEAD_DAYS: 14,
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
    ConfigKey.OBJECTION_CATEGORIES: _DEFAULT_OBJECTION_CATEGORIES,
    ConfigKey.CONVERSATION_IDLE_CLOSE_MINUTES: 120,
}


class BotConfigSchema(_StrictModel):
    """Typed view over the JSONB override bag — validated at write time."""

    no_answer: NoAnswerConfig = Field(default_factory=lambda: NoAnswerConfig())
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
    business: BusinessConfig = Field(default_factory=lambda: BusinessConfig())
    delivery: DeliveryConfig = Field(default_factory=lambda: DeliveryConfig())
    agent: AgentConfig = Field(default_factory=lambda: AgentConfig())
    objections: ObjectionsConfig = Field(default_factory=lambda: ObjectionsConfig())
    conversation: ConversationConfig = Field(default_factory=lambda: ConversationConfig())


class NoAnswerConfig(_StrictModel):
    first_reminder_min: int = Field(120, ge=30, le=480)
    second_reminder_min: int = Field(1440, ge=720, le=2880)
    max_followups: int = Field(2, ge=1, le=4)
    # Optional text overrides; None falls back to the worker's built-in copy.
    first_reminder_text: str | None = Field(default=None, max_length=1000)
    second_reminder_text: str | None = Field(default=None, max_length=1000)


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


class ScoringConfig(_StrictModel):
    hot_threshold: int = Field(80, ge=50, le=100)
    cold_threshold: int = Field(30, ge=0, le=50)


class ABTestConfig(_StrictModel):
    default_split: list[int] = Field(default_factory=lambda: [50, 50])
    min_sample: int = Field(100, ge=50, le=1000)


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
    min_score: float = Field(0.7, ge=0.5, le=0.9)


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


class PrivacyConfig(_StrictModel):
    retention_months: int = Field(24, ge=6, le=60)


class BookingConfig(_StrictModel):
    default_calendar_id: str | None = None
    default_duration_min: int = Field(30, ge=15, le=240)
    lookahead_days: int = Field(14, ge=1, le=60)


class ObjectionsConfig(_StrictModel):
    # The category vocabulary the objection classifier maps to (UC-13).
    categories: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_OBJECTION_CATEGORIES), max_length=30
    )


class ConversationConfig(_StrictModel):
    # Minutes of inactivity after which a conversation is auto-closed and its
    # objections extracted (UC-13 sweep).
    idle_close_minutes: int = Field(120, ge=15, le=10080)


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
    bubble_max_chars: int = Field(600, ge=80, le=1000)


class AgentConfig(_StrictModel):
    """Agent reasoning loop (Amalia-style tool-use). When enabled the
    orchestrator can call read-only tools mid-turn (live availability, upcoming
    appointments) and adapt its reply to the real result before sending."""

    tool_use_enabled: bool = True
    # Total LLM calls allowed per turn. 1 = single-shot (no tool grounding);
    # 3 allows up to two tool round-trips.
    max_tool_iterations: int = Field(3, ge=1, le=5)
