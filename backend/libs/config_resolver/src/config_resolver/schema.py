"""Config schema — the typed surface for the three-level cascade.

Keys map 1:1 to the table in section 9.4 of reloop-ai-architettura.md.
Adding a new configurable knob means: add a key here, add the default,
run the OpenAPI codegen to sync the frontend.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ConfigKey(StrEnum):
    # UC-03 No answer
    NO_ANSWER_FIRST_REMINDER_MIN = "no_answer.first_reminder_min"
    NO_ANSWER_SECOND_REMINDER_MIN = "no_answer.second_reminder_min"
    NO_ANSWER_MAX_FOLLOWUPS = "no_answer.max_followups"

    # UC-06 Reactivation
    REACTIVATION_DORMANT_DAYS = "reactivation.dormant_days"
    REACTIVATION_INTERVAL_DAYS = "reactivation.interval_days"
    REACTIVATION_MAX_ATTEMPTS = "reactivation.max_attempts"

    # UC-04 Pipeline
    PIPELINE_ADVANCE_THRESHOLD = "pipeline.advance_threshold"
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

    # UC-07 RAG
    RAG_TOP_K = "rag.top_k"
    RAG_MIN_SCORE = "rag.min_score"

    # Bot
    BOT_LANGUAGE = "bot.language"
    BOT_TONE = "bot.tone"

    # Escalation
    ESCALATION_ENABLED = "escalation.enabled"

    # Privacy
    PRIVACY_RETENTION_MONTHS = "privacy.retention_months"

    # UC-02 Booking
    BOOKING_DEFAULT_CALENDAR_ID = "booking.default_calendar_id"
    BOOKING_DEFAULT_DURATION_MIN = "booking.default_duration_min"
    BOOKING_LOOKAHEAD_DAYS = "booking.lookahead_days"


SYSTEM_DEFAULTS: dict[ConfigKey, Any] = {
    ConfigKey.NO_ANSWER_FIRST_REMINDER_MIN: 120,
    ConfigKey.NO_ANSWER_SECOND_REMINDER_MIN: 1440,
    ConfigKey.NO_ANSWER_MAX_FOLLOWUPS: 2,
    ConfigKey.REACTIVATION_DORMANT_DAYS: 90,
    ConfigKey.REACTIVATION_INTERVAL_DAYS: 7,
    ConfigKey.REACTIVATION_MAX_ATTEMPTS: 3,
    ConfigKey.PIPELINE_ADVANCE_THRESHOLD: 60,
    ConfigKey.PIPELINE_QUALIFIED_STAGE_ID: None,
    ConfigKey.SCORING_HOT_THRESHOLD: 80,
    ConfigKey.SCORING_COLD_THRESHOLD: 30,
    ConfigKey.AB_DEFAULT_SPLIT: [50, 50],
    ConfigKey.AB_MIN_SAMPLE: 100,
    ConfigKey.SCHEDULE_ACTIVE_HOURS: "24/7",
    ConfigKey.SCHEDULE_OFF_HOURS_MESSAGE: "Grazie per averci contattato! Ti risponderemo al più presto.",
    ConfigKey.SCHEDULE_TIMEZONE: "Europe/Rome",
    ConfigKey.RAG_TOP_K: 5,
    ConfigKey.RAG_MIN_SCORE: 0.7,
    ConfigKey.BOT_LANGUAGE: "it",
    ConfigKey.BOT_TONE: "professionale-amichevole",
    ConfigKey.ESCALATION_ENABLED: True,
    ConfigKey.PRIVACY_RETENTION_MONTHS: 24,
    ConfigKey.BOOKING_DEFAULT_CALENDAR_ID: None,
    ConfigKey.BOOKING_DEFAULT_DURATION_MIN: 30,
    ConfigKey.BOOKING_LOOKAHEAD_DAYS: 14,
}


class BotConfigSchema(BaseModel):
    """Typed view over the JSONB override bag — validated at write time."""

    no_answer: "NoAnswerConfig" = Field(default_factory=lambda: NoAnswerConfig())
    reactivation: "ReactivationConfig" = Field(default_factory=lambda: ReactivationConfig())
    pipeline: "PipelineConfig" = Field(default_factory=lambda: PipelineConfig())
    scoring: "ScoringConfig" = Field(default_factory=lambda: ScoringConfig())
    ab_test: "ABTestConfig" = Field(default_factory=lambda: ABTestConfig())
    schedule: "ScheduleConfig" = Field(default_factory=lambda: ScheduleConfig())
    rag: "RagConfig" = Field(default_factory=lambda: RagConfig())
    bot: "BotSurfaceConfig" = Field(default_factory=lambda: BotSurfaceConfig())
    escalation: "EscalationConfig" = Field(default_factory=lambda: EscalationConfig())
    privacy: "PrivacyConfig" = Field(default_factory=lambda: PrivacyConfig())
    booking: "BookingConfig" = Field(default_factory=lambda: BookingConfig())


class NoAnswerConfig(BaseModel):
    first_reminder_min: int = Field(120, ge=30, le=480)
    second_reminder_min: int = Field(1440, ge=720, le=2880)
    max_followups: int = Field(2, ge=1, le=4)


class ReactivationConfig(BaseModel):
    dormant_days: int = Field(90, ge=30, le=180)
    interval_days: int = Field(7, ge=3, le=30)
    max_attempts: int = Field(3, ge=1, le=5)


class PipelineConfig(BaseModel):
    advance_threshold: int = Field(60, ge=0, le=100)
    qualified_stage_id: str | None = None


class ScoringConfig(BaseModel):
    hot_threshold: int = Field(80, ge=50, le=100)
    cold_threshold: int = Field(30, ge=0, le=50)


class ABTestConfig(BaseModel):
    default_split: list[int] = Field(default_factory=lambda: [50, 50])
    min_sample: int = Field(100, ge=50, le=1000)


class ScheduleConfig(BaseModel):
    active_hours: str = "24/7"
    off_hours_message: str = "Grazie per averci contattato! Ti risponderemo al più presto."
    timezone: str = "Europe/Rome"


class RagConfig(BaseModel):
    top_k: int = Field(5, ge=3, le=10)
    min_score: float = Field(0.7, ge=0.5, le=0.9)


class BotSurfaceConfig(BaseModel):
    language: str = "it"
    tone: str = "professionale-amichevole"


class EscalationConfig(BaseModel):
    enabled: bool = True


class PrivacyConfig(BaseModel):
    retention_months: int = Field(24, ge=6, le=60)


class BookingConfig(BaseModel):
    default_calendar_id: str | None = None
    default_duration_min: int = Field(30, ge=15, le=240)
    lookahead_days: int = Field(14, ge=1, le=60)
