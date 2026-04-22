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
