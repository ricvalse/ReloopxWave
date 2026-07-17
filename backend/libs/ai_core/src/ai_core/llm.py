"""LLM client abstraction — every provider implements the same Protocol."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True, frozen=True)
class ImagePart:
    """A base64-encoded image attached to a user turn for vision.

    Kept as a separate optional field on `ChatMessage` (rather than widening
    `content` to a union) so every existing `.content` string operation across
    the router / playground / FT export keeps working untouched — only the two
    provider serializers below look at `image`. `mime` is the normalized media
    type (e.g. `image/jpeg`); each provider emits its own block dialect.
    """

    mime: str
    b64: str


@dataclass(slots=True, frozen=True)
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str
    # Optional vision attachment on a user turn. None for every text-only
    # message (the overwhelming majority), so serialization is unchanged there.
    image: ImagePart | None = None


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


def _openai_content(m: ChatMessage) -> Any:
    """OpenAI chat-completions content: a bare string, or a parts list carrying
    an `image_url` data-URI when the turn has a vision attachment."""
    if m.image is None:
        return m.content
    parts: list[dict[str, Any]] = []
    if m.content:
        parts.append({"type": "text", "text": m.content})
    parts.append(
        {
            "type": "image_url",
            "image_url": {"url": f"data:{m.image.mime};base64,{m.image.b64}"},
        }
    )
    return parts


def _anthropic_content(m: ChatMessage) -> Any:
    """Anthropic messages content: a bare string, or a blocks list with a native
    base64 `image` block (Meta/Amalia shape) plus the text."""
    if m.image is None:
        return m.content
    return [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": m.image.mime, "data": m.image.b64},
        },
        {"type": "text", "text": m.content or "(immagine ricevuta)"},
    ]


def _model_locks_temperature(model: str) -> bool:
    """OpenAI GPT-5 models only accept the default temperature (1)."""
    bare = model[3:] if model.startswith("ft:") else model
    return bare.startswith("gpt-5")


def _model_uses_completion_tokens(model: str) -> bool:
    """GPT-5 / reasoning models reject `max_tokens` (400) and require
    `max_completion_tokens` instead. Same family as the temperature lock
    (incl. fine-tunes `ft:gpt-5-…`), so match the prefix permissively."""
    bare = model[3:] if model.startswith("ft:") else model
    return bare.startswith("gpt-5")


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
            "messages": [{"role": m.role, "content": _openai_content(m)} for m in messages],
        }
        # GPT-5 family rejects any non-default temperature with a 400. We
        # also use the same family for fine-tunes (`ft:gpt-5-…`), so match
        # the prefix permissively.
        if not _model_locks_temperature(self.model):
            payload["temperature"] = temperature
        if max_tokens is not None:
            # GPT-5 / reasoning models renamed this param; sending the old
            # `max_tokens` to them is a hard 400. Pick the right key per model.
            if _model_uses_completion_tokens(self.model):
                payload["max_completion_tokens"] = max_tokens
            else:
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

    def __init__(
        self, *, api_key: str, model: str = "claude-sonnet-4-6", timeout: float = 30.0
    ) -> None:
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

        # System turns are always text (images only ride user turns), so the join
        # is safe; user/assistant turns may carry a vision block.
        system_text = "\n\n".join(m.content for m in messages if m.role == "system")
        user_turns = [
            {"role": m.role, "content": _anthropic_content(m)}
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

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        return CompletionResult(
            content=text,
            model=resp.model,
            tokens_in=resp.usage.input_tokens,
            tokens_out=resp.usage.output_tokens,
            latency_ms=latency_ms,
            raw=resp.model_dump() if hasattr(resp, "model_dump") else {},
        )
