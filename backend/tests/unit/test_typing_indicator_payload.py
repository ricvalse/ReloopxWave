"""The 360dialog typing-indicator call posts the exact Cloud-API read+typing
payload to /messages with the D360 key header."""

from __future__ import annotations

from typing import Any

from integrations.whatsapp.d360_client import D360WhatsAppClient


class _Resp:
    status_code = 200
    text = ""

    def json(self) -> dict[str, Any]:
        return {"success": True}


class _FakeHttp:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    async def post(self, path: str, *, json: dict[str, Any], headers: dict[str, str]) -> _Resp:
        self.posts.append({"path": path, "json": json, "headers": headers})
        return _Resp()

    async def aclose(self) -> None:
        return None


async def test_send_typing_indicator_payload() -> None:
    http = _FakeHttp()
    client = D360WhatsAppClient(api_key="KEY-123", phone_number_id="PNID", http=http)

    await client.send_typing_indicator(message_id="wamid.in.42")

    assert len(http.posts) == 1
    sent = http.posts[0]
    assert sent["path"] == "/messages"
    assert sent["headers"]["D360-API-KEY"] == "KEY-123"
    assert sent["json"] == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.in.42",
        "typing_indicator": {"type": "text"},
    }
