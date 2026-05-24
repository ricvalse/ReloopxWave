"""Regression tests for the router → platform notify endpoint.

The bug: `POST /internal/whatsapp-connected` is authenticated by HMAC
signature, not a Supabase JWT, but an earlier version declared
`session: DBSession` in the handler. That dependency resolves through
`get_tenant_context` → `verify_supabase_jwt`, which FastAPI runs *before*
the handler body — so a correctly-signed router notify (no bearer token)
was rejected with `403 missing_token` before the signature check ran.

These tests pin the contract: the route must gate on the signature alone
and must never require a bearer token. We monkeypatch the DB layer so no
live Postgres is needed.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import ClassVar
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import internal
from integrations.router import SIGNATURE_HEADER, sign_router_payload
from shared import Settings

_SECRET = "test-shared-secret"
_PLATFORM = "wavemarketing"


class _FakeRepo:
    """Records upsert calls instead of touching Postgres."""

    calls: ClassVar[list[dict]] = []

    def __init__(self, session: object, *, kek_base64: str) -> None:
        pass

    async def upsert_whatsapp(self, **kwargs) -> None:
        _FakeRepo.calls.append(kwargs)


@asynccontextmanager
async def _fake_session_scope():
    yield object()  # the fake repo ignores the session


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _FakeRepo.calls = []
    settings = Settings(
        router_shared_secret=_SECRET,
        router_platform_id=_PLATFORM,
        integrations_kek_base64="",
    )
    monkeypatch.setattr(internal, "get_settings", lambda: settings)
    monkeypatch.setattr(internal, "session_scope", _fake_session_scope)
    monkeypatch.setattr(internal, "IntegrationRepository", _FakeRepo)

    app = FastAPI()
    app.include_router(internal.router, prefix="/internal")
    return TestClient(app)


def _signed(body: dict) -> tuple[bytes, str]:
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    return raw, sign_router_payload(raw_body=raw, shared_secret=_SECRET)


def test_no_token_is_not_required(client: TestClient) -> None:
    """The regression guard: a request with no Authorization header must NOT
    be rejected with `missing_token`. Without a signature it's a 401 signature
    failure — never a 403 bearer-token failure."""
    resp = client.post("/internal/whatsapp-connected", content=b"{}")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "invalid signature"}
    # The old bug surfaced as this exact shape — make sure it's gone.
    assert "missing_token" not in resp.text


def test_valid_signature_persists_channel(client: TestClient) -> None:
    merchant_id = str(uuid4())
    body = {
        "event": "whatsapp.connected",
        "platform_id": _PLATFORM,
        "customer_id": merchant_id,
        "channels": [
            {"phone_number_id": "pnid-1", "channel_api_key": "D360-KEY"}
        ],
    }
    raw, sig = _signed(body)
    resp = client.post(
        "/internal/whatsapp-connected",
        content=raw,
        headers={"Content-Type": "application/json", SIGNATURE_HEADER: sig},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"received": 1, "event": "whatsapp.connected"}
    assert len(_FakeRepo.calls) == 1
    assert _FakeRepo.calls[0]["phone_number_id"] == "pnid-1"
    assert _FakeRepo.calls[0]["api_key"] == "D360-KEY"
    assert str(_FakeRepo.calls[0]["merchant_id"]) == merchant_id


def test_unknown_event_rejected(client: TestClient) -> None:
    """A signed payload with an unsupported event must log + return 400 — not
    blow up on the structlog `event=` keyword collision."""
    raw, sig = _signed({
        "event": "whatsapp.exploded",
        "platform_id": _PLATFORM,
        "customer_id": str(uuid4()),
        "channels": [],
    })
    resp = client.post(
        "/internal/whatsapp-connected",
        content=raw,
        headers={SIGNATURE_HEADER: sig},
    )
    assert resp.status_code == 400
    assert not _FakeRepo.calls


def test_bad_signature_rejected(client: TestClient) -> None:
    raw, _ = _signed({"event": "whatsapp.connected", "platform_id": _PLATFORM,
                      "customer_id": str(uuid4()), "channels": []})
    resp = client.post(
        "/internal/whatsapp-connected",
        content=raw,
        headers={SIGNATURE_HEADER: "sha256=deadbeef"},
    )
    assert resp.status_code == 401
    assert not _FakeRepo.calls
