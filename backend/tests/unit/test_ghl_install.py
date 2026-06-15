"""GHL marketplace INSTALL/UNINSTALL worker handlers.

INSTALL: resolve the tenant from companyId, record the pending location, mint a
location token, persist it. Missing agency → dropped (no retry storm).
UNINSTALL: revoke the location token (soft delete).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from workers.conversation import handlers as mod


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        integrations_kek_base64="kek",
        ghl_client_id="cid",
        ghl_client_secret="cs",
    )


@asynccontextmanager
async def _fake_scope():
    yield object()


async def test_handle_ghl_install_mints_and_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid.uuid4()
    installs: list[dict] = []
    tokens: list[dict] = []

    monkeypatch.setattr(mod, "get_settings", _settings)
    monkeypatch.setattr(mod, "session_scope", _fake_scope)

    class FakeRepo:
        def __init__(self, session, *, kek_base64): ...

        async def resolve_agency_by_company_id(self, company_id):
            return SimpleNamespace(
                tenant_id=tenant_id,
                company_id=company_id,
                access_token="AGENCY_AT",
                refresh_token="AGENCY_RT",
                expires_at=9999999999,
                status="active",
                company_name="ACME",
            )

        async def upsert_location_install(self, **kw):
            installs.append(kw)

        async def set_location_token(self, **kw):
            tokens.append(kw)

    monkeypatch.setattr(mod, "GHLMarketplaceRepository", FakeRepo)

    async def fake_mint(*, agency_access_token, company_id, location_id):
        return SimpleNamespace(
            access_token="LAT",
            refresh_token="LRT",
            expires_at=123,
            location_id=location_id,
            company_id=company_id,
        )

    monkeypatch.setattr(mod, "mint_location_token", fake_mint)

    async def fake_name(settings, token, location_id):
        return "Pizzeria Roma"

    monkeypatch.setattr(mod, "_fetch_location_name", fake_name)

    result = await mod.handle_ghl_install(
        {},
        {"type": "INSTALL", "locationId": "loc-9", "companyId": "comp-1", "userId": "u-1"},
    )

    assert result == {"installed": True, "location_id": "loc-9"}
    assert tokens[0]["location_id"] == "loc-9"
    assert tokens[0]["access_token"] == "LAT"
    # pending row created first, then the name backfilled.
    assert installs[0]["tenant_id"] == tenant_id
    assert any(kw.get("location_name") == "Pizzeria Roma" for kw in installs)


async def test_handle_ghl_install_no_agency_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", _settings)
    monkeypatch.setattr(mod, "session_scope", _fake_scope)

    class FakeRepo:
        def __init__(self, session, *, kek_base64): ...

        async def resolve_agency_by_company_id(self, company_id):
            return None

        async def upsert_location_install(self, **kw):  # pragma: no cover
            raise AssertionError("should not record a location without an agency")

    monkeypatch.setattr(mod, "GHLMarketplaceRepository", FakeRepo)

    result = await mod.handle_ghl_install(
        {}, {"type": "INSTALL", "locationId": "loc-9", "companyId": "unknown"}
    )
    assert result == {"installed": False, "reason": "no_agency_install"}


async def test_handle_ghl_install_missing_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", _settings)
    result = await mod.handle_ghl_install({}, {"type": "INSTALL"})
    assert result == {"installed": False, "reason": "missing_ids"}


async def test_handle_ghl_uninstall_revokes(monkeypatch: pytest.MonkeyPatch) -> None:
    revoked: list[str] = []
    monkeypatch.setattr(mod, "get_settings", _settings)
    monkeypatch.setattr(mod, "session_scope", _fake_scope)

    class FakeRepo:
        def __init__(self, session, *, kek_base64): ...

        async def revoke_location(self, location_id):
            revoked.append(location_id)
            return True

    monkeypatch.setattr(mod, "GHLMarketplaceRepository", FakeRepo)

    result = await mod.handle_ghl_uninstall({}, "loc-9")
    assert result == {"revoked": True, "location_id": "loc-9"}
    assert revoked == ["loc-9"]
