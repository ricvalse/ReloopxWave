"""Unit tests for the scheduler logic that is pure (no DB):

  * integration_health._check_one — the per-provider state calculation
    (WhatsApp = green unless already error; GHL = green unless the token has
    been expired for > 24h);
  * analytics_export._json_compact — the CSV property serialisation smoke test.

The DB-bound aggregations (kpi_rollup, the SELECTs in integration_health and
analytics_export) are exercised by the integration suite under Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from workers.scheduler.analytics_export import _json_compact
from workers.scheduler.integration_health import _check_one


def _integration(provider: str, *, status: str = "active", expires_at: datetime | None = None):
    return SimpleNamespace(provider=provider, status=status, expires_at=expires_at)


def _settings():
    return SimpleNamespace()


# --- integration_health._check_one -----------------------------------------


async def test_whatsapp_active_is_healthy() -> None:
    ok = await _check_one(
        _integration("whatsapp", status="active"), http=None, settings=_settings()
    )
    assert ok is True


async def test_whatsapp_already_error_is_unhealthy() -> None:
    ok = await _check_one(_integration("whatsapp", status="error"), http=None, settings=_settings())
    assert ok is False


async def test_ghl_without_expiry_is_healthy() -> None:
    ok = await _check_one(_integration("ghl", expires_at=None), http=None, settings=_settings())
    assert ok is True


async def test_ghl_recently_expired_is_still_healthy() -> None:
    # Expired 1h ago — refresh should have handled it; not yet broken.
    recent = datetime.now(tz=UTC) - timedelta(hours=1)
    ok = await _check_one(_integration("ghl", expires_at=recent), http=None, settings=_settings())
    assert ok is True


async def test_ghl_long_expired_is_unhealthy() -> None:
    # Expired > 24h ago — the refresh flow clearly failed.
    stale = datetime.now(tz=UTC) - timedelta(days=2)
    ok = await _check_one(_integration("ghl", expires_at=stale), http=None, settings=_settings())
    assert ok is False


async def test_unknown_provider_defaults_healthy() -> None:
    ok = await _check_one(_integration("other"), http=None, settings=_settings())
    assert ok is True


# --- analytics_export._json_compact ----------------------------------------


def test_json_compact_is_separator_tight_and_unicode_safe() -> None:
    out = _json_compact({"count": 3, "label": "città"})
    assert out == '{"count":3,"label":"città"}'  # no spaces, no \u escapes


def test_json_compact_empty_dict() -> None:
    assert _json_compact({}) == "{}"
