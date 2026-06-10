"""GHL OAuth callback must NOT require a Supabase JWT.

The callback is a browser redirect from GHL (no Authorization header). It used
to take the tenant-scoped `DBSession` dependency, which runs JWT verification
and 403'd every merchant trying to connect their CRM. The merchant identity
comes from the signed `state`, so the handler now uses an unscoped service-role
session. This test calls the handler directly with only (code, state) — proving
no JWT/tenant context is needed — and asserts the integration is persisted and a
302 redirect is returned.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


async def test_ghl_oauth_callback_requires_no_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.routers import integrations as mod

    merchant_id = uuid.uuid4()
    upserts: list[dict] = []

    monkeypatch.setattr(
        mod,
        "get_settings",
        lambda: SimpleNamespace(
            ghl_oauth_state_secret="state-secret",
            ghl_client_secret="client-secret",
            ghl_client_id="client-id",
            integrations_kek_base64="kek",
        ),
    )
    monkeypatch.setattr(
        mod, "verify_oauth_state", lambda state, *, secret: SimpleNamespace(merchant_id=merchant_id)
    )

    async def fake_exchange(*, code, client_id, client_secret, redirect_uri):
        return SimpleNamespace(
            access_token="AT", refresh_token="RT", expires_at=123, location_id="loc-1"
        )

    monkeypatch.setattr(mod, "exchange_authorization_code", fake_exchange)
    monkeypatch.setattr(mod, "_ghl_redirect_uri", lambda settings: "https://api/cb")
    monkeypatch.setattr(
        mod, "_merchant_redirect", lambda settings, **kw: "https://portal/connected"
    )

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    monkeypatch.setattr(mod, "session_scope", fake_session_scope)

    class FakeRepo:
        def __init__(self, session, *, kek_base64): ...
        async def upsert_ghl(self, **kw):
            upserts.append(kw)

    monkeypatch.setattr(mod, "IntegrationRepository", FakeRepo)

    # Called with ONLY code+state — no session, no ctx, no JWT.
    resp = await mod.ghl_oauth_callback(code="auth-code", state="signed-state")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://portal/connected"
    assert len(upserts) == 1
    assert upserts[0]["merchant_id"] == merchant_id
    assert upserts[0]["access_token"] == "AT"
