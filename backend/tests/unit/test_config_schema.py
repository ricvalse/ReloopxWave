import pytest
from pydantic import ValidationError

from config_resolver.schema import SYSTEM_DEFAULTS, BotConfigSchema, ConfigKey


def test_system_defaults_cover_every_key() -> None:
    missing = {k for k in ConfigKey if k not in SYSTEM_DEFAULTS}
    assert not missing, f"system defaults missing: {missing}"


def test_bot_config_schema_applies_bounds() -> None:
    cfg = BotConfigSchema.model_validate(
        {"no_answer": {"first_reminder_min": 120, "max_followups": 2}}
    )
    assert cfg.no_answer.first_reminder_min == 120
    assert cfg.scoring.hot_threshold == 80  # default


def test_persona_defaults() -> None:
    cfg = BotConfigSchema()
    assert cfg.bot.formality == "auto"
    assert cfg.bot.verbosity == "equilibrato"
    assert cfg.bot.emoji_policy == "sobrio"
    assert cfg.bot.sentiment_adaptation_enabled is True
    assert cfg.bot.do_phrases == []
    assert cfg.bot.examples == []


def test_structured_persona_round_trips() -> None:
    cfg = BotConfigSchema.model_validate(
        {
            "bot": {
                "formality": "dai-del-lei",
                "verbosity": "conciso",
                "emoji_policy": "mai",
                "do_phrases": ["volentieri"],
                "examples": [{"q": "Quanto costa?", "a": "Dipende."}],
            }
        }
    )
    assert cfg.bot.formality == "dai-del-lei"
    assert cfg.bot.examples[0].q == "Quanto costa?"


def test_invalid_enum_rejected() -> None:
    with pytest.raises(ValidationError):
        BotConfigSchema.model_validate({"bot": {"formality": "nope"}})


def test_legacy_tone_still_accepted() -> None:
    cfg = BotConfigSchema.model_validate({"bot": {"tone": "formale e distaccato"}})
    assert cfg.bot.tone == "formale e distaccato"
    assert cfg.bot.formality == "auto"  # untouched


def test_delivery_defaults_are_noop() -> None:
    cfg = BotConfigSchema()
    assert cfg.delivery.debounce_window_s == 0
    assert cfg.delivery.typing_indicator_enabled is False
    assert cfg.delivery.multi_bubble_max == 1
    assert cfg.delivery.typing_delay_max_s == 0.0


def test_delivery_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        BotConfigSchema.model_validate({"delivery": {"multi_bubble_max": 9}})
    with pytest.raises(ValidationError):
        BotConfigSchema.model_validate({"delivery": {"debounce_window_s": 999}})
