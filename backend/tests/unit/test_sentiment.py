"""Sentiment analyzer — label parsing tolerance and safe fallback (spec 6.6)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from ai_core.sentiment import SentimentAnalyzer


@dataclass
class _Result:
    content: str
    model: str = "gpt-5-nano"
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


class _FakeClient:
    model = "gpt-5-nano"

    def __init__(self, content: str = "", raise_exc: bool = False) -> None:
        self._content = content
        self._raise = raise_exc

    async def complete(self, *, messages, response_format=None):
        if self._raise:
            raise RuntimeError("boom")
        return _Result(content=self._content)


class _FakeRouter:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    async def select(self, req):
        assert req.purpose == "sentiment"
        return self._client


async def _run(content: str = "", *, raise_exc: bool = False, text: str = "ciao") -> str:
    analyzer = SentimentAnalyzer(_FakeRouter(_FakeClient(content, raise_exc)))  # type: ignore[arg-type]
    return await analyzer.analyze(merchant_id=uuid4(), tenant_id=uuid4(), text=text)


async def test_clean_label() -> None:
    assert await _run("positive") == "positive"


async def test_tolerates_extra_words_and_punctuation() -> None:
    assert await _run("Sentiment: NEGATIVE.") == "negative"


async def test_unknown_defaults_to_neutral() -> None:
    assert await _run("boh") == "neutral"


async def test_llm_failure_defaults_to_neutral() -> None:
    assert await _run(raise_exc=True) == "neutral"


async def test_empty_text_is_neutral_without_calling_model() -> None:
    assert await _run("positive", text="   ") == "neutral"
