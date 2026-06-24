"""Integration health-check worker — GHL location liveness (#24).

GHL no longer lives in the `integrations` table (ADR 0007 — it's in
`ghl_location_tokens`). These tests cover the new branch that iterates linked
location tokens, pings each with a cheap GHL call, and stamps health on the row.
The DB + GHL client are faked; this is a pure unit test of the orchestration.

The probe is *resilient*: a transient blip (network/timeout/5xx/rate-limit) on
the once-a-day probe must NOT flip a valid token to `error` (which would disable
all live GHL ops for that merchant for ~24h). Only a DEFINITIVE auth failure
(refresh-token rejected / 401) marks the location `error`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, ClassVar

import pytest
import workers.scheduler.integration_health as mod

from shared import IntegrationError


@dataclass
class FakeLoc:
    location_id: str
    access_token: str = "AT"
    refresh_token: str = "RT"
    expires_at: int = 0


class FakeRepo:
    """One shared instance backing every `GHLMarketplaceRepository(...)` call."""

    def __init__(self, locations: list[FakeLoc]) -> None:
        self._locations = locations
        # (location_id, healthy, mark_error) per mark call.
        self.marks: list[tuple[str, bool, bool | None]] = []

    def __call__(self, session: Any, *, kek_base64: str) -> FakeRepo:
        return self

    async def list_health_checkable_locations(self) -> list[FakeLoc]:
        return self._locations

    async def mark_location_health(
        self, *, location_id: str, healthy: bool, mark_error: bool | None = None
    ) -> bool:
        self.marks.append((location_id, healthy, mark_error))
        return True

    async def set_location_token(self, **kwargs: Any) -> None:
        return None


class FakeGHLClient:
    """Configurable failure injection per location id.

    - ids in `transient_ids` raise a non-auth error (network/5xx/rate-limit).
    - ids in `auth_ids` raise the definitive `ghl_refresh_failed` IntegrationError.
    - ids in `unauthorized_ids` raise a 401 `ghl_request_failed` IntegrationError.
    """

    transient_ids: ClassVar[set[str]] = set()
    auth_ids: ClassVar[set[str]] = set()
    unauthorized_ids: ClassVar[set[str]] = set()

    def __init__(self, *, token_bundle: Any, **kwargs: Any) -> None:
        self._loc = token_bundle.location_id

    async def get_location(self, location_id: str) -> dict[str, Any]:
        if location_id in FakeGHLClient.auth_ids:
            raise IntegrationError("refresh dead", error_code="ghl_refresh_failed")
        if location_id in FakeGHLClient.unauthorized_ids:
            raise IntegrationError("unauthorized", error_code="ghl_request_failed", status=401)
        if location_id in FakeGHLClient.transient_ids:
            raise RuntimeError("boom")
        return {"id": location_id}

    async def close(self) -> None:
        return None


@dataclass
class _Bundle:
    access_token: str
    refresh_token: str
    expires_at: int
    location_id: str


class FakeSettings:
    ghl_client_id = "cid"
    ghl_client_secret = "csec"
    integrations_kek_base64 = "kek"


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_scope() -> Any:
        yield object()

    monkeypatch.setattr(mod, "session_scope", fake_scope)
    monkeypatch.setattr(mod, "GHLClient", FakeGHLClient)
    monkeypatch.setattr(mod, "GHLTokenBundle", lambda **kw: _Bundle(**kw))
    FakeGHLClient.transient_ids = set()
    FakeGHLClient.auth_ids = set()
    FakeGHLClient.unauthorized_ids = set()


def _install_repo(monkeypatch: pytest.MonkeyPatch, repo: FakeRepo) -> None:
    monkeypatch.setattr(mod, "GHLMarketplaceRepository", repo)


async def test_ghl_health_all_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo([FakeLoc("loc-1"), FakeLoc("loc-2")])
    _install_repo(monkeypatch, repo)

    checked, broken = await mod._check_ghl_locations(FakeSettings())

    assert (checked, broken) == (2, 0)
    assert repo.marks == [("loc-1", True, False), ("loc-2", True, False)]


async def test_transient_failure_does_not_mark_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient probe failure counts as unhealthy in the stamp but must NOT
    flip the location to `error` — `mark_error` stays False so a valid token
    keeps serving live GHL operations."""
    FakeGHLClient.transient_ids = {"loc-2"}
    repo = FakeRepo([FakeLoc("loc-1"), FakeLoc("loc-2")])
    _install_repo(monkeypatch, repo)

    checked, broken = await mod._check_ghl_locations(FakeSettings())

    assert (checked, broken) == (2, 1)
    assert ("loc-1", True, False) in repo.marks
    # unhealthy=True (recorded), but mark_error=False (do NOT disable the token).
    assert ("loc-2", False, False) in repo.marks


async def test_definitive_auth_failure_marks_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A definitive auth failure (refresh-token rejected) DOES flip the location
    to `error` so the merchant portal surfaces a reconnect."""
    FakeGHLClient.auth_ids = {"loc-2"}
    repo = FakeRepo([FakeLoc("loc-1"), FakeLoc("loc-2")])
    _install_repo(monkeypatch, repo)

    checked, broken = await mod._check_ghl_locations(FakeSettings())

    assert (checked, broken) == (2, 1)
    assert ("loc-1", True, False) in repo.marks
    assert ("loc-2", False, True) in repo.marks


async def test_unauthorized_after_refresh_marks_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 that survives the client's transparent refresh+retry is treated as
    a definitive auth failure too (the rotated token is still unauthorized)."""
    FakeGHLClient.unauthorized_ids = {"loc-1"}
    repo = FakeRepo([FakeLoc("loc-1")])
    _install_repo(monkeypatch, repo)

    checked, broken = await mod._check_ghl_locations(FakeSettings())

    assert (checked, broken) == (1, 1)
    assert ("loc-1", False, True) in repo.marks


async def test_ghl_health_skips_when_no_credentials() -> None:
    class NoCreds(FakeSettings):
        ghl_client_id = ""

    checked, broken = await mod._check_ghl_locations(NoCreds())
    assert (checked, broken) == (0, 0)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (IntegrationError("x", error_code="ghl_refresh_failed"), True),
        (IntegrationError("x", error_code="ghl_request_failed", status=401), True),
        (IntegrationError("x", error_code="ghl_request_failed", status=403), True),
        (IntegrationError("x", error_code="ghl_request_failed", status=500), False),
        (IntegrationError("x", error_code="ghl_request_failed", status=429), False),
        (IntegrationError("x", error_code="ghl_request_failed"), False),
        (RuntimeError("network blip"), False),
        (TimeoutError(), False),
    ],
)
def test_is_definitive_auth_failure(exc: BaseException, expected: bool) -> None:
    assert mod._is_definitive_auth_failure(exc) is expected
