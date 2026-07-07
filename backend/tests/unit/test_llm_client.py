"""OpenAIClient payload mapping — regression per il bug max_tokens (findings E2E 2026-07-07).

I modelli GPT-5 / reasoning rifiutano `max_tokens` con un 400 e vogliono
`max_completion_tokens`. Prima del fix il client inviava sempre `max_tokens`,
facendo fallire in silenzio HyDE, re-ranking RAG e objection extraction.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_core.llm import ChatMessage, OpenAIClient


class _FakeCompletions:
    def __init__(self) -> None:
        self.last_payload: dict | None = None

    async def create(self, **payload):
        self.last_payload = payload
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            model=payload["model"],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            model_dump=lambda: {},
        )


def _client_with_fake(model: str) -> tuple[OpenAIClient, _FakeCompletions]:
    client = OpenAIClient(api_key="test", model=model)
    fake = _FakeCompletions()
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return client, fake


@pytest.mark.asyncio
async def test_gpt5_uses_max_completion_tokens() -> None:
    client, fake = _client_with_fake("gpt-5-mini")
    await client.complete(messages=[ChatMessage(role="user", content="hi")], max_tokens=150)
    assert fake.last_payload is not None
    assert fake.last_payload.get("max_completion_tokens") == 150
    assert "max_tokens" not in fake.last_payload
    # GPT-5 blocca anche la temperature custom.
    assert "temperature" not in fake.last_payload


@pytest.mark.asyncio
async def test_gpt5_finetune_uses_max_completion_tokens() -> None:
    client, fake = _client_with_fake("ft:gpt-5-mini:acme:abc123")
    await client.complete(messages=[ChatMessage(role="user", content="hi")], max_tokens=42)
    assert fake.last_payload is not None
    assert fake.last_payload.get("max_completion_tokens") == 42
    assert "max_tokens" not in fake.last_payload


@pytest.mark.asyncio
async def test_legacy_model_keeps_max_tokens() -> None:
    client, fake = _client_with_fake("gpt-4.1-mini")
    await client.complete(
        messages=[ChatMessage(role="user", content="hi")], max_tokens=99, temperature=0.3
    )
    assert fake.last_payload is not None
    assert fake.last_payload.get("max_tokens") == 99
    assert "max_completion_tokens" not in fake.last_payload
    assert fake.last_payload.get("temperature") == 0.3


@pytest.mark.asyncio
async def test_no_token_cap_sends_neither() -> None:
    client, fake = _client_with_fake("gpt-5-mini")
    await client.complete(messages=[ChatMessage(role="user", content="hi")])
    assert fake.last_payload is not None
    assert "max_tokens" not in fake.last_payload
    assert "max_completion_tokens" not in fake.last_payload
