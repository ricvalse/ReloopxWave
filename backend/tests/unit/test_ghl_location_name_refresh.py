"""Agency-side backfill of a GHL sub-account display name.

The name is fetched best-effort at INSTALL and can end up null (fetch failed, or
the location predates that code). `POST /integrations/ghl/locations/{id}/refresh-name`
lets an agency admin re-pull it on demand. These tests drive the handler
directly with monkeypatched module deps (no DB, no live GHL).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from shared import IntegrationError, NotFoundError


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        integrations_kek_base64="kek",
        ghl_client_id="client-id",
        ghl_client_secret="client-secret",
    )


def _ctx(tenant_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        role="agency_admin", tenant_id=tenant_id, merchant_id=None, actor_id=uuid.uuid4()
    )


def _token(tenant_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id=tenant_id,
        company_id="comp-1",
        access_token="AT",
        refresh_token="RT",
        expires_at=0,
    )


class _FakeClient:
    def __init__(self, *, resp: dict | None = None, exc: Exception | None = None) -> None:
        self._resp = resp or {}
        self._exc = exc
        self.closed = False

    async def get_location(self, location_id: str) -> dict:
        if self._exc is not None:
            raise self._exc
        return self._resp

    async def close(self) -> None:
        self.closed = True


def _install(monkeypatch: pytest.MonkeyPatch, *, token, client):
    from api.routers import integrations as mod

    upserts: list[dict] = []

    class FakeRepo:
        def __init__(self, session, *, kek_base64) -> None: ...

        async def resolve_location_token_any_status(self, location_id):
            return token

        async def upsert_location_install(self, **kw):
            upserts.append(kw)

    monkeypatch.setattr(mod, "get_settings", _settings)
    monkeypatch.setattr(mod, "GHLMarketplaceRepository", FakeRepo)
    monkeypatch.setattr(mod, "GHLClient", lambda **kw: client)

    @asynccontextmanager
    async def fake_scope():
        yield object()

    monkeypatch.setattr(mod, "session_scope", fake_scope)
    return mod, upserts


async def test_refresh_name_success_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid.uuid4()
    client = _FakeClient(resp={"location": {"name": "Pizzeria Roma"}})
    mod, upserts = _install(monkeypatch, token=_token(tenant_id), client=client)

    out = await mod.ghl_refresh_location_name(
        location_id="LOC-1", ctx=_ctx(tenant_id), session=object()
    )

    assert out.refreshed is True
    assert out.location_name == "Pizzeria Roma"
    assert out.detail is None
    assert client.closed is True
    assert len(upserts) == 1
    assert upserts[0]["location_name"] == "Pizzeria Roma"
    assert upserts[0]["location_id"] == "LOC-1"
    assert upserts[0]["tenant_id"] == tenant_id
    assert upserts[0]["company_id"] == "comp-1"


async def test_refresh_name_ghl_error_returns_hint_no_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid.uuid4()
    client = _FakeClient(exc=IntegrationError("boom", error_code="ghl_unauthorized"))
    mod, upserts = _install(monkeypatch, token=_token(tenant_id), client=client)

    out = await mod.ghl_refresh_location_name(
        location_id="LOC-1", ctx=_ctx(tenant_id), session=object()
    )

    assert out.refreshed is False
    assert out.location_name is None
    assert out.detail  # non-empty hint for the UI
    assert upserts == []
    assert client.closed is True


async def test_refresh_name_network_error_is_graceful_no_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # GHL unreachable / timeout raises a raw (non-IntegrationError) exception,
    # like httpx.ConnectError. Must degrade to refreshed=False, not 500.
    tenant_id = uuid.uuid4()
    client = _FakeClient(exc=RuntimeError("connection refused"))
    mod, upserts = _install(monkeypatch, token=_token(tenant_id), client=client)

    out = await mod.ghl_refresh_location_name(
        location_id="LOC-1", ctx=_ctx(tenant_id), session=object()
    )

    assert out.refreshed is False
    assert out.location_name is None
    assert out.detail
    assert upserts == []
    assert client.closed is True


async def test_refresh_name_no_name_returned_no_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid.uuid4()
    client = _FakeClient(resp={"location": {"timezone": "Europe/Rome"}})
    mod, upserts = _install(monkeypatch, token=_token(tenant_id), client=client)

    out = await mod.ghl_refresh_location_name(
        location_id="LOC-1", ctx=_ctx(tenant_id), session=object()
    )

    assert out.refreshed is False
    assert out.location_name is None
    assert upserts == []  # no name → nothing to persist


async def test_refresh_name_unknown_location_404(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid.uuid4()
    mod, _ = _install(monkeypatch, token=None, client=_FakeClient(resp={}))

    with pytest.raises(NotFoundError):
        await mod.ghl_refresh_location_name(
            location_id="LOC-X", ctx=_ctx(tenant_id), session=object()
        )


async def test_refresh_name_cross_tenant_404(monkeypatch: pytest.MonkeyPatch) -> None:
    caller_tenant = uuid.uuid4()
    other_tenant = uuid.uuid4()
    client = _FakeClient(resp={"location": {"name": "Non tua"}})
    mod, upserts = _install(monkeypatch, token=_token(other_tenant), client=client)

    with pytest.raises(NotFoundError):
        await mod.ghl_refresh_location_name(
            location_id="LOC-1", ctx=_ctx(caller_tenant), session=object()
        )
    assert upserts == []  # never touched another tenant's row
