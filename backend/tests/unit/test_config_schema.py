import pytest
from pydantic import ValidationError

from config_resolver.schema import SYSTEM_DEFAULTS, BotConfigSchema, ConfigKey


def test_system_defaults_cover_every_key() -> None:
    missing = {k for k in ConfigKey if k not in SYSTEM_DEFAULTS}
    assert not missing, f"system defaults missing: {missing}"


def test_bot_config_schema_applies_bounds() -> None:
    cfg = BotConfigSchema.model_validate({"escalation": {"sla_minutes": 30}})
    assert cfg.escalation.sla_minutes == 30
    assert cfg.scoring.hot_threshold == 80  # default


def test_no_answer_section_is_gone() -> None:
    """La cadenza dei follow-up vive sulla lavagnetta (ADR 0014/0015): le vecchie
    chiavi `no_answer.*` erano esposte nel pannello merchant senza che nessuno le
    leggesse. Restare rifiutate è ciò che impedisce di ri-esporle per sbaglio."""
    assert "no_answer" not in BotConfigSchema.model_fields
    assert not [k for k in ConfigKey if k.value.startswith("no_answer.")]
    with pytest.raises(ValidationError):
        BotConfigSchema.model_validate({"no_answer": {"first_reminder_min": 120}})


def test_handoff_knobs_are_configurable() -> None:
    """I due parametri dell'handoff erano fuori dalla cascata: la SLA viveva in una
    variabile d'ambiente globale (stessa per ogni merchant) e la pausa dopo un
    messaggio dal telefono era una costante nel codice."""
    cfg = BotConfigSchema.model_validate(
        {"escalation": {"sla_minutes": 45, "phone_echo_pause_minutes": 30}}
    )
    assert cfg.escalation.sla_minutes == 45
    assert cfg.escalation.phone_echo_pause_minutes == 30
    # Un valore non positivo spingerebbe il cutoff nel futuro e farebbe risultare
    # scaduto ogni handoff aperto: il bound lo impedisce a monte.
    with pytest.raises(ValidationError):
        BotConfigSchema.model_validate({"escalation": {"sla_minutes": 0}})


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


def test_delivery_defaults_are_human_feel() -> None:
    # The product default is now human-feel out of the box (debounce, typing
    # indicator, brief pause, a couple of bubbles). Merchants can dial any of
    # these back to 0/False via the cascade to restore instant single-send.
    cfg = BotConfigSchema()
    assert cfg.delivery.debounce_window_s == 8
    assert cfg.delivery.typing_indicator_enabled is True
    assert cfg.delivery.multi_bubble_max == 2
    assert cfg.delivery.typing_delay_max_s == 6.0


def test_delivery_can_be_dialed_back_to_instant_send() -> None:
    cfg = BotConfigSchema.model_validate(
        {
            "delivery": {
                "debounce_window_s": 0,
                "typing_indicator_enabled": False,
                "typing_delay_max_s": 0.0,
                "multi_bubble_max": 1,
            }
        }
    )
    assert cfg.delivery.debounce_window_s == 0
    assert cfg.delivery.typing_indicator_enabled is False
    assert cfg.delivery.multi_bubble_max == 1


def test_agent_defaults_enable_tool_loop() -> None:
    cfg = BotConfigSchema()
    assert cfg.agent.tool_use_enabled is True
    assert cfg.agent.max_tool_iterations == 3


def test_delivery_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        BotConfigSchema.model_validate({"delivery": {"multi_bubble_max": 9}})
    with pytest.raises(ValidationError):
        BotConfigSchema.model_validate({"delivery": {"debounce_window_s": 999}})


def test_ghl_contact_sync_defaults_and_round_trip() -> None:
    cfg = BotConfigSchema()
    assert cfg.ghl.contact_field_map == {}
    assert cfg.ghl.contact_default_tags == []

    cfg = BotConfigSchema.model_validate(
        {
            "ghl": {
                "contact_field_map": {"budget": "cf-123"},
                "contact_default_tags": ["whatsapp-lead"],
            }
        }
    )
    assert cfg.ghl.contact_field_map == {"budget": "cf-123"}
    assert cfg.ghl.contact_default_tags == ["whatsapp-lead"]
