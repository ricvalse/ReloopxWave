"""Sentiment-driven prompt adaptation — uses the PRIOR turn's lead.sentiment."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from ai_core.conversation_service import ActionDispatcher, ConversationService
from config_resolver import ConfigKey

_NEG = "insoddisfatto"
_POS = "ben disposto"


def _resolver_cls(values: dict):
    class _R:
        def __init__(self, session, redis=None) -> None:
            pass

        async def resolve(self, key, *, merchant_id):
            return values.get(key)

    return _R


async def _build(monkeypatch, values, prior_sentiment) -> str:
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


_BIZ = {ConfigKey.BUSINESS_NAME: "Studio Rossi"}


async def test_negative_prior_injects_empathy(monkeypatch) -> None:
    prompt = await _build(monkeypatch, _BIZ, prior_sentiment="negative")
    assert _NEG in prompt


async def test_positive_prior_injects_upsell(monkeypatch) -> None:
    prompt = await _build(monkeypatch, _BIZ, prior_sentiment="positive")
    assert _POS in prompt


@pytest.mark.parametrize("prior", ["neutral", None, "unknown"])
async def test_neutral_or_missing_injects_nothing(monkeypatch, prior) -> None:
    prompt = await _build(monkeypatch, _BIZ, prior_sentiment=prior)
    assert _NEG not in prompt
    assert _POS not in prompt


async def test_flag_off_suppresses_even_negative(monkeypatch) -> None:
    values = {**_BIZ, ConfigKey.BOT_SENTIMENT_ADAPTATION_ENABLED: False}
    prompt = await _build(monkeypatch, values, prior_sentiment="negative")
    assert _NEG not in prompt
