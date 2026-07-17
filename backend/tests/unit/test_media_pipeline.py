"""Unit tests for inbound-media support: MIME/policy helpers, the 360dialog
two-step download (with the load-bearing CDN host swap), the LLM vision
serialization, and the schema-hint media-note swap."""

from __future__ import annotations

import base64
from typing import ClassVar

import httpx
import pytest

from ai_core.llm import (
    ChatMessage,
    ImagePart,
    OpenAIClient,
    _anthropic_content,
    _openai_content,
)
from ai_core.orchestrator import render_schema_hint
from integrations.whatsapp import media as m
from integrations.whatsapp.d360_client import D360WhatsAppClient

# ---- media.py policy helpers -------------------------------------------------


def test_ext_for_mime_known_and_fallback() -> None:
    assert m.ext_for_mime("image/jpeg") == ".jpg"
    assert m.ext_for_mime("audio/ogg; codecs=opus") == ".ogg"
    assert m.ext_for_mime("application/pdf") == ".pdf"
    assert m.ext_for_mime(None) == ".bin"
    assert m.ext_for_mime("application/x-totally-unknown") == ".bin"


def test_mime_allow_and_vision() -> None:
    assert m.is_allowed_mime("image/jpeg")
    assert m.is_allowed_mime("audio/ogg; codecs=opus")  # param stripped
    assert not m.is_allowed_mime("application/x-msdownload")
    assert m.is_vision_mime("image/png")
    assert not m.is_vision_mime("application/pdf")


def test_size_cap_by_kind() -> None:
    assert m.max_bytes_for_kind("document") == m.MAX_DOCUMENT_BYTES
    assert m.max_bytes_for_kind("image") == m.MAX_MEDIA_BYTES
    assert m.MAX_DOCUMENT_BYTES <= 20 * 1024 * 1024  # ≤ the Supabase bucket limit


# ---- D360 two-step download + CDN host swap ---------------------------------


@pytest.mark.asyncio
async def test_download_media_swaps_lookaside_host() -> None:
    """Step 1 returns a lookaside CDN URL; the client must rewrite the host to
    the 360dialog proxy (which injects the FB token) before fetching the bytes.
    Both hops carry the D360-API-KEY."""
    seen_urls: list[str] = []
    seen_keys: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        seen_keys.append(request.headers.get("D360-API-KEY"))
        if request.url.path == "/MID":
            return httpx.Response(
                200,
                json={
                    "url": "https://lookaside.fbsbx.com/whatsapp_business/attachments/xyz?token=abc",
                    "mime_type": "image/jpeg",
                },
            )
        return httpx.Response(200, content=b"JPEGBYTES", headers={"content-type": "image/jpeg"})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://waba-v2.360dialog.io", transport=transport)
    client = D360WhatsAppClient(api_key="KEY", phone_number_id="42", http=http)

    result = await client.download_media(media_id="MID")
    await client.close()

    assert result is not None
    data, mime = result
    assert data == b"JPEGBYTES"
    assert mime == "image/jpeg"
    # The binary fetch must hit the 360dialog host, NOT lookaside.
    assert "lookaside.fbsbx.com" not in seen_urls[1]
    assert seen_urls[1].startswith("https://waba-v2.360dialog.io/")
    assert seen_keys == ["KEY", "KEY"]


@pytest.mark.asyncio
async def test_download_media_returns_none_on_missing_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"mime_type": "image/jpeg"})  # no url

    http = httpx.AsyncClient(
        base_url="https://waba-v2.360dialog.io", transport=httpx.MockTransport(handler)
    )
    client = D360WhatsAppClient(api_key="KEY", phone_number_id="42", http=http)
    assert await client.download_media(media_id="MID") is None
    await client.close()


@pytest.mark.asyncio
async def test_download_media_returns_none_on_metadata_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    http = httpx.AsyncClient(
        base_url="https://waba-v2.360dialog.io", transport=httpx.MockTransport(handler)
    )
    client = D360WhatsAppClient(api_key="KEY", phone_number_id="42", http=http)
    assert await client.download_media(media_id="MID") is None
    await client.close()


# ---- LLM vision serialization ------------------------------------------------


def test_openai_content_shapes() -> None:
    assert _openai_content(ChatMessage(role="user", content="ciao")) == "ciao"
    parts = _openai_content(
        ChatMessage(
            role="user", content="che modello?", image=ImagePart(mime="image/jpeg", b64="QUJD")
        )
    )
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": "che modello?"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"


def test_anthropic_content_shapes() -> None:
    assert _anthropic_content(ChatMessage(role="user", content="ciao")) == "ciao"
    blocks = _anthropic_content(
        ChatMessage(role="user", content="", image=ImagePart(mime="image/png", b64="QUJD"))
    )
    assert isinstance(blocks, list)
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"] == {"type": "base64", "media_type": "image/png", "data": "QUJD"}
    assert blocks[1] == {"type": "text", "text": "(immagine ricevuta)"}  # empty text fallback


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = type("M", (), {"content": content})()


class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 5


class _FakeResp:
    model = "gpt-5-mini"
    choices: ClassVar = [_FakeChoice("ok")]
    usage = _FakeUsage()

    def model_dump(self) -> dict:
        return {}


@pytest.mark.asyncio
async def test_openai_client_forwards_image_block() -> None:
    """The image ChatMessage must serialize into the payload the SDK receives."""
    captured: dict = {}

    class _FakeCompletions:
        async def create(self, **kwargs: object) -> _FakeResp:
            captured.update(kwargs)
            return _FakeResp()

    class _FakeClient:
        chat = type("C", (), {"completions": _FakeCompletions()})()

    client = OpenAIClient(api_key="k", model="gpt-5-mini")
    client._client = _FakeClient()  # type: ignore[assignment]
    b64 = base64.standard_b64encode(b"IMG").decode()
    await client.complete(
        messages=[
            ChatMessage(role="user", content="guarda", image=ImagePart(mime="image/jpeg", b64=b64))
        ]
    )
    content = captured["messages"][0]["content"]
    assert isinstance(content, list)
    assert any(p.get("type") == "image_url" for p in content)


# ---- schema-hint media-note swap --------------------------------------------


def test_schema_hint_media_note_swap() -> None:
    """Default keeps the 'you can't see media' note; viewable_media swaps it for
    the vision directive — and the default stays byte-identical."""
    text_hint = render_schema_hint(None)
    assert "NON puoi vedere" in text_hint
    vision_hint = render_schema_hint(None, viewable_media=True)
    assert "la stai vedendo" in vision_hint
    assert "NON puoi vedere" not in vision_hint
    # Byte-identity of the default is pinned elsewhere; assert the flag is the
    # only difference source here.
    assert text_hint == render_schema_hint(None, viewable_media=False)
