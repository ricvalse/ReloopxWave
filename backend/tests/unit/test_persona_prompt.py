"""Snapshot-ish tests for the structured persona system-prompt builder.

We drive `_cascade_system_prompt` directly with a fake ConfigResolver seeded
from a dict, so each structured enum value maps to a deterministic fragment.
"""

from __future__ import annotations

import uuid
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from ai_core.conversation_service import (
    DEFAULT_SYSTEM_PROMPT,
    ActionDispatcher,
    ConversationService,
)
from config_resolver import ConfigKey


def _resolver_cls(values: dict):
    class _R:
        def __init__(self, session, redis=None) -> None:
            pass

        async def resolve(self, key, *, merchant_id):
            return values.get(key)

    return _R


async def _build(monkeypatch: pytest.MonkeyPatch, values: dict, prior_sentiment=None) -> str:
    from ai_core import conversation_service as cs

    monkeypatch.setattr(cs, "ConfigResolver", _resolver_cls(values))
    svc = ConversationService(
        orchestrator=AsyncMock(),
        action_dispatcher=ActionDispatcher(),
        reply_sender=AsyncMock(),
        embedder=None,
        kek_base64="x",
    )
    return await svc._cascade_system_prompt(
        session=object(), merchant_id=uuid.uuid4(), prior_sentiment=prior_sentiment
    )


# A minimal "has_profile" trigger so the assembled prompt is built (not DEFAULT).
_BIZ = {ConfigKey.BUSINESS_NAME: "Studio Rossi"}


async def test_empty_config_uses_default_prompt(monkeypatch) -> None:
    assert await _build(monkeypatch, {}) == DEFAULT_SYSTEM_PROMPT


async def test_formality_tu(monkeypatch) -> None:
    prompt = await _build(monkeypatch, {**_BIZ, ConfigKey.BOT_FORMALITY: "dai-del-tu"})
    assert "dando del tu" in prompt


async def test_formality_lei(monkeypatch) -> None:
    prompt = await _build(monkeypatch, {**_BIZ, ConfigKey.BOT_FORMALITY: "dai-del-lei"})
    assert "dando del Lei" in prompt


async def test_formality_auto_falls_back_to_legacy_tone(monkeypatch) -> None:
    prompt = await _build(
        monkeypatch,
        {**_BIZ, ConfigKey.BOT_FORMALITY: "auto", ConfigKey.BOT_TONE: "scherzoso e diretto"},
    )
    assert "Mantieni un tono scherzoso e diretto." in prompt
    assert "dando del" not in prompt


async def test_legacy_only_tone_preserves_tone_clause(monkeypatch) -> None:
    """A merchant who only set bot.tone (no structured fields) still gets the
    legacy tone clause — backward compatibility."""
    prompt = await _build(monkeypatch, {**_BIZ, ConfigKey.BOT_TONE: "formale e distaccato"})
    assert "Mantieni un tono formale e distaccato." in prompt


@pytest.mark.parametrize(
    ("value", "needle"),
    [
        ("conciso", "molto brevi"),
        ("equilibrato", "lunghezza equilibrata"),
        ("dettagliato", "più articolate"),
    ],
)
async def test_verbosity_fragments(monkeypatch, value, needle) -> None:
    prompt = await _build(monkeypatch, {**_BIZ, ConfigKey.BOT_VERBOSITY: value})
    assert needle in prompt


@pytest.mark.parametrize(
    ("value", "needle"),
    [
        ("mai", "Non usare mai emoji."),
        ("sobrio", "con parsimonia"),
        ("libero", "liberamente"),
    ],
)
async def test_emoji_fragments(monkeypatch, value, needle) -> None:
    prompt = await _build(monkeypatch, {**_BIZ, ConfigKey.BOT_EMOJI_POLICY: value})
    assert needle in prompt


async def test_do_and_dont_phrases(monkeypatch) -> None:
    prompt = await _build(
        monkeypatch,
        {
            **_BIZ,
            ConfigKey.BOT_DO_PHRASES: ["a presto", "con piacere"],
            ConfigKey.BOT_DONT_PHRASES: ["non lo so"],
        },
    )
    assert "da preferire: a presto; con piacere." in prompt
    assert "da evitare: non lo so." in prompt


async def test_greeting_and_signature(monkeypatch) -> None:
    prompt = await _build(
        monkeypatch,
        {
            **_BIZ,
            ConfigKey.BOT_GREETING_STYLE: "saluta col nome",
            ConfigKey.BOT_SIGNATURE: "— Team Rossi",
        },
    )
    assert "Stile di apertura: saluta col nome" in prompt
    assert "firma quando appropriato: — Team Rossi" in prompt


async def test_examples_few_shot_block(monkeypatch) -> None:
    prompt = await _build(
        monkeypatch,
        {**_BIZ, ConfigKey.BOT_EXAMPLES: [{"q": "Quanto costa?", "a": "Dipende dal servizio."}]},
    )
    assert "Esempi di stile" in prompt
    assert "Cliente: «Quanto costa?» → Tu: «Dipende dal servizio.»" in prompt


async def test_first_message_reaches_prompt(monkeypatch) -> None:
    """The merchant's welcome message (`bot.first_message`) must be injected as
    opening guidance so the agent actually uses it (previously orphaned)."""
    prompt = await _build(
        monkeypatch,
        {**_BIZ, ConfigKey.BOT_FIRST_MESSAGE: "Ciao! Come posso aiutarti oggi?"},
    )
    assert "messaggio di benvenuto" in prompt
    assert "Ciao! Come posso aiutarti oggi?" in prompt


async def test_first_message_alone_triggers_assembled_prompt(monkeypatch) -> None:
    """A merchant who set only the welcome message still opts into the assembled
    prompt (not the generic default)."""
    prompt = await _build(monkeypatch, {ConfigKey.BOT_FIRST_MESSAGE: "Benvenuto da noi!"})
    assert prompt != DEFAULT_SYSTEM_PROMPT
    assert "Benvenuto da noi!" in prompt


async def test_persona_field_alone_triggers_assembled_prompt(monkeypatch) -> None:
    """Setting only a persona field (no business profile) still produces the
    assembled prompt, not the generic default."""
    prompt = await _build(monkeypatch, {ConfigKey.BOT_DO_PHRASES: ["volentieri"]})
    assert prompt != DEFAULT_SYSTEM_PROMPT
    assert "da preferire: volentieri." in prompt


async def test_store_policies_injected(monkeypatch) -> None:
    """Store policies are injected as a deterministic block and, on their own,
    trigger the assembled prompt (not the generic default)."""
    from ai_core import conversation_service as cs

    class _FakePolicy:
        shipping_info = "Spedizione gratuita sopra 49€"
        return_policy = "Reso entro 30 giorni"
        payment_methods = None
        exchange_policy = None
        warranty_info = None
        contact_info = None
        custom_policies: ClassVar[list] = [
            {"title": "Confezioni", "body": "Confezione regalo su richiesta"}
        ]

    class _FakeRepo:
        def __init__(self, session) -> None:
            pass

        async def get_for_merchant(self, merchant_id):
            return _FakePolicy()

    monkeypatch.setattr(cs, "StorePolicyRepository", _FakeRepo)
    prompt = await _build(monkeypatch, {})
    assert prompt != DEFAULT_SYSTEM_PROMPT
    assert "Politiche del negozio:" in prompt
    assert "Spedizioni: Spedizione gratuita sopra 49€" in prompt
    assert "Resi e rimborsi: Reso entro 30 giorni" in prompt
    assert "Confezioni: Confezione regalo su richiesta" in prompt
    # Unset fields produce no line.
    assert "Pagamenti:" not in prompt


async def test_extras_are_last(monkeypatch) -> None:
    prompt = await _build(
        monkeypatch,
        {
            **_BIZ,
            ConfigKey.BOT_FORMALITY: "dai-del-tu",
            ConfigKey.BOT_SYSTEM_PROMPT_ADDITIONS: "ISTRUZIONE-CUSTOM-XYZ",
        },
    )
    assert "Istruzioni aggiuntive dal merchant:" in prompt
    # The freeform escape hatch wins: it sits after the structured fragments.
    assert prompt.index("dando del tu") < prompt.index("ISTRUZIONE-CUSTOM-XYZ")
