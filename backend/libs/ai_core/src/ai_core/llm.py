"""LLM client abstraction — every provider implements the same Protocol."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True, frozen=True)
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str


@dataclass(slots=True, frozen=True)
class CompletionResult:
    content: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    raw: dict[str, Any]


class LLMClient(Protocol):
    model: str

    async def complete(
        self,
        *,
        messages: Sequence[ChatMessage],
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> CompletionResult: ...


class OpenAIClient:
    """Wraps `openai` SDK. Supports fine-tuned models by passing their id as `model`."""

    def __init__(self, *, api_key: str, model: str, timeout: float = 30.0) -> None:
        self._api_key = api_key
        self.model = model
        self._timeout = timeout
        # Lazy import keeps the dependency optional during tests that stub the client.
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self._api_key, timeout=self._timeout)
        return self._client

    async def complete(
        self,
        *,
        messages: Sequence[ChatMessage],
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        import time

        client = self._get_client()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format

        t0 = time.monotonic()
        resp = await client.chat.completions.create(**payload)
        latency_ms = int((time.monotonic() - t0) * 1000)

        choice = resp.choices[0]
        usage = resp.usage
        return CompletionResult(
            content=choice.message.content or "",
            model=resp.model,
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
            raw=resp.model_dump() if hasattr(resp, "model_dump") else {},
        )


class AnthropicClient:
    """Fallback provider. Gated by feature flag."""

    def __init__(self, *, api_key: str, model: str = "claude-sonnet-4-6", timeout: float = 30.0) -> None:
        self._api_key = api_key
        self.model = model
        self._timeout = timeout
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=self._api_key, timeout=self._timeout)
        return self._client

    async def complete(
        self,
        *,
        messages: Sequence[ChatMessage],
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        import time

        system_text = "\n\n".join(m.content for m in messages if m.role == "system")
        user_turns = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in {"user", "assistant"}
        ]

        client = self._get_client()
        t0 = time.monotonic()
        resp = await client.messages.create(
            model=self.model,
            system=system_text or None,
            messages=user_turns,
            max_tokens=max_tokens or 1024,
            temperature=temperature,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        return CompletionResult(
            content=text,
            model=resp.model,
            tokens_in=resp.usage.input_tokens,
            tokens_out=resp.usage.output_tokens,
            latency_ms=latency_ms,
            raw=resp.model_dump() if hasattr(resp, "model_dump") else {},
        )
