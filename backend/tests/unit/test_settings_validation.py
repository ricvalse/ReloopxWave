"""Production fail-fast: required secrets must be present (and not localhost)
before a production process is allowed to boot. Local/staging boot freely."""

from __future__ import annotations

import pytest

from shared.settings import Settings


def _prod(**overrides) -> Settings:
    base = {"environment": "production", **overrides}
    # _env_file=None so a stray .env in the tree can't make the test flaky.
    return Settings(_env_file=None, **base)


def test_local_never_blocks() -> None:
    Settings(_env_file=None, environment="local").ensure_production_ready()  # no raise


def test_production_missing_secrets_raises() -> None:
    s = _prod()
    errors = s.production_config_errors()
    assert any("integrations_kek_base64" in e for e in errors)
    assert any("openai_api_key" in e for e in errors)
    # localhost defaults for db/redis are flagged too.
    assert any("supabase_db_url" in e for e in errors)
    assert any("redis_url" in e for e in errors)
    with pytest.raises(RuntimeError):
        s.ensure_production_ready()


def test_production_fully_configured_passes() -> None:
    s = _prod(
        supabase_url="https://x.supabase.co",
        supabase_anon_key="anon",
        supabase_service_role_key="service",
        supabase_jwt_secret="jwt",
        openai_api_key="sk-x",
        integrations_kek_base64="a2Vr",
        supabase_db_url="postgresql+asyncpg://u:p@db.example:5432/postgres",
        redis_url="redis://cache.example:6379/0",
    )
    assert s.production_config_errors() == []
    s.ensure_production_ready()  # no raise


def test_recommended_warnings_list_integration_creds() -> None:
    warnings = _prod().production_config_warnings()
    assert any("ghl_client_id" in w for w in warnings)
    assert any("router_base_url" in w for w in warnings)
