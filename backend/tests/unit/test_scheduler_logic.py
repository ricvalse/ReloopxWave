"""Unit tests for the scheduler logic that is pure (no DB):

  * integration_health._check_one — the per-provider state calculation for the
    `integrations` table (WhatsApp = green unless already error; any other
    provider falls through to green). GHL is NOT health-checked here: its tokens
    live in `ghl_location_tokens` and are probed by _check_ghl_location /
    _is_definitive_auth_failure (covered by test_integration_health.py).
  * analytics_export._json_compact — the CSV property serialisation smoke test.

The DB-bound aggregations (kpi_rollup, the SELECTs in integration_health and
analytics_export) are exercised by the integration suite under Postgres.
"""

from __future__ import annotations

from datetime import datetime
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


async def test_unknown_provider_defaults_healthy() -> None:
    ok = await _check_one(_integration("other"), http=None, settings=_settings())
    assert ok is True


# --- analytics_export._json_compact ----------------------------------------


def test_json_compact_is_separator_tight_and_unicode_safe() -> None:
    out = _json_compact({"count": 3, "label": "città"})
    assert out == '{"count":3,"label":"città"}'  # no spaces, no \u escapes


def test_json_compact_empty_dict() -> None:
    assert _json_compact({}) == "{}"
