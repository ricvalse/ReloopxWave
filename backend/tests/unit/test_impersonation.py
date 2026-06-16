"""Unit tests for agency→merchant impersonation token minting.

The security claim these pin: a token minted by `mint_impersonation_token`
- verifies through `verify_supabase_jwt` on the HS256 path with the same
  `supabase_jwt_secret`, and
- decodes via `get_tenant_context` into a merchant-scoped context flagged as an
  impersonation (so RLS behaves as the merchant, and the lock-bypass + audit
  paths can react).

No DB or Redis needed — minting + verification are pure.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from jose import jwt

from api.dependencies import auth as auth_mod
from integrations.impersonation import mint_impersonation_token
from shared import PermissionDeniedError, Settings

_SECRET = "unit-test-jwt-secret"


def _mint(secret: str = _SECRET, **overrides):
    kwargs = dict(
        jwt_secret=secret,
        supabase_url="https://proj.supabase.co",
        admin_user_id=uuid4(),
        admin_email="admin@agency.example",
        tenant_id=uuid4(),
        merchant_id=uuid4(),
        merchant_name="Studio Rossi",
    )
    kwargs.update(overrides)
    return mint_impersonation_token(**kwargs)


def test_minted_token_has_dual_path_claims() -> None:
    admin = uuid4()
    tenant = uuid4()
    merchant = uuid4()
    tok = _mint(admin_user_id=admin, tenant_id=tenant, merchant_id=merchant)

    claims = jwt.decode(tok.access_token, _SECRET, algorithms=["HS256"], audience="authenticated")

    # Top-level (backend + RLS).
    assert claims["tenant_id"] == str(tenant)
    assert claims["merchant_id"] == str(merchant)
    assert claims["user_role"] == "merchant_user"
    # Postgres role for PostgREST stays "authenticated" (NOT the app role).
    assert claims["role"] == "authenticated"
    # app_metadata for the web-merchant layout.
    assert claims["app_metadata"]["merchant_id"] == str(merchant)
    assert claims["app_metadata"]["tenant_id"] == str(tenant)
    assert claims["app_metadata"]["role"] == "merchant_user"
    # Impersonation marker.
    assert claims["act"]["type"] == "impersonation"
    assert claims["act"]["sub"] == str(admin)
    assert claims["session_id"] == str(tok.session_id)


def test_minted_token_verifies_and_yields_impersonation_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = uuid4()
    tenant = uuid4()
    merchant = uuid4()
    tok = _mint(admin_user_id=admin, tenant_id=tenant, merchant_id=merchant)

    settings = Settings(_env_file=None, supabase_jwt_secret=_SECRET)
    monkeypatch.setattr(auth_mod, "get_settings", lambda: settings)

    import anyio

    async def _run() -> None:
        claims = await auth_mod.verify_supabase_jwt(authorization=f"Bearer {tok.access_token}")
        ctx = await auth_mod.get_tenant_context(claims)
        assert ctx.tenant_id == tenant
        assert ctx.merchant_id == merchant
        assert ctx.role == "merchant_user"
        assert ctx.is_impersonation is True
        assert ctx.impersonator_id == admin
        assert ctx.impersonation_session_id == tok.session_id

    anyio.run(_run)


def test_normal_token_is_not_flagged_impersonation() -> None:
    import anyio

    async def _run() -> None:
        claims = {
            "tenant_id": str(uuid4()),
            "merchant_id": str(uuid4()),
            "user_role": "merchant_user",
            "sub": str(uuid4()),
        }
        ctx = await auth_mod.get_tenant_context(claims)
        assert ctx.is_impersonation is False
        assert ctx.impersonator_id is None

    anyio.run(_run)


def test_ttl_is_clamped() -> None:
    too_long = _mint(ttl_seconds=99999)
    assert too_long.expires_in == 1800  # max clamp
    too_short = _mint(ttl_seconds=1)
    assert too_short.expires_in == 300  # min clamp


def test_empty_secret_raises() -> None:
    with pytest.raises(ValueError):
        _mint(secret="")


def test_missing_secret_token_rejected_by_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token signed with a different secret must not verify."""
    tok = _mint(secret="attacker-secret")
    settings = Settings(_env_file=None, supabase_jwt_secret=_SECRET)
    monkeypatch.setattr(auth_mod, "get_settings", lambda: settings)

    import anyio

    async def _run() -> None:
        with pytest.raises(PermissionDeniedError):
            await auth_mod.verify_supabase_jwt(authorization=f"Bearer {tok.access_token}")

    anyio.run(_run)
