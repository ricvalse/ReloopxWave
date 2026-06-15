"""GHL agency OAuth callback must NOT require a Supabase JWT.

The callback is a browser redirect from GHL (no Authorization header). It uses
an unscoped service-role session; the tenant identity comes from the signed
`state`. This test calls the handler directly with only (code, state) — proving
no JWT/tenant context is needed — and asserts the agency install is persisted
with the Company token and a 302 redirect is returned.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


async def test_ghl_agency_callback_requires_no_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.routers import integrations as mod

    tenant_id = uuid.uuid4()
    upserts: list[dict] = []

    monkeypatch.setattr(
        mod,
        "get_settings",
        lambda: SimpleNamespace(
            ghl_oauth_state_secret="state-secret",
            ghl_client_secret="client-secret",
            ghl_client_id="client-id",
            integrations_kek_base64="kek",
            public_web_admin_url="https://admin",
        ),
    )
    monkeypatch.setattr(
        mod, "verify_oauth_state", lambda state, *, secret: SimpleNamespace(tenant_id=tenant_id)
    )

    async def fake_exchange(*, code, client_id, client_secret, redirect_uri, user_type):
        assert user_type == "Company"
        return SimpleNamespace(
            access_token="AT",
            refresh_token="RT",
            expires_at=123,
            location_id=None,
            company_id="comp-1",
            user_type="Company",
            raw={"companyName": "ACME Agency"},
        )

    monkeypatch.setattr(mod, "exchange_authorization_code", fake_exchange)
    monkeypatch.setattr(mod, "_ghl_redirect_uri", lambda settings: "https://api/cb")

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    monkeypatch.setattr(mod, "session_scope", fake_session_scope)

    class FakeRepo:
        def __init__(self, session, *, kek_base64): ...
        async def upsert_agency_install(self, **kw):
            upserts.append(kw)

    monkeypatch.setattr(mod, "GHLMarketplaceRepository", FakeRepo)

    # Called with ONLY code+state — no session, no ctx, no JWT.
    resp = await mod.ghl_oauth_callback(code="auth-code", state="signed-state")

    assert resp.status_code == 302
    assert (
        resp.headers["Location"]
        == "https://admin/integrations?provider=ghl_agency&status=connected"
    )
    assert len(upserts) == 1
    assert upserts[0]["tenant_id"] == tenant_id
    assert upserts[0]["company_id"] == "comp-1"
    assert upserts[0]["access_token"] == "AT"
    assert upserts[0]["company_name"] == "ACME Agency"
