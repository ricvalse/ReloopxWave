"""JWT verification + tenant-context extraction.

Supabase projects sign access tokens in one of two ways:

1. **Asymmetric (ES256 via JWKS)** — the modern default. Tokens carry a `kid`
   in the header that indexes into the project's JWKS at
   `$SUPABASE_URL/auth/v1/.well-known/jwks.json`. This is the path we hit in
   production.
2. **Symmetric (HS256 with a shared secret)** — legacy / local Supabase CLI.
   Kept as a fallback so integration tests and older projects still work.

We pick between them by reading the `alg` claim in the token header. The JWKS
is fetched lazily and cached in-process for `_JWKS_TTL_SECONDS` — the verify
path stays fast (sync dict lookup) and survives Supabase rotating keys without
a redeploy (TTL-bounded miss forces a refetch).

The custom claims `tenant_id`, `merchant_id`, and `role` are written by a
Supabase Auth hook (see migration `0002_auth_jwt_hook.py`) and passed through
here.
"""
from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import Depends, Header
from jose import jwk, jwt
from jose.exceptions import JWTError
from jose.utils import base64url_decode

from db.session import TenantContext
from shared import PermissionDeniedError, get_logger, get_settings

logger = get_logger(__name__)

_JWKS_TTL_SECONDS = 600  # 10 minutes — Supabase rotates rarely; cache miss is cheap
_jwks_cache: dict[str, Any] = {"keys": {}, "fetched_at": 0.0}
_jwks_lock = asyncio.Lock()


def _jwks_url(supabase_url: str) -> str:
    return f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


async def _load_jwks(supabase_url: str) -> dict[str, dict[str, Any]]:
    """Return `{ kid: jwk_dict }`, refreshing the cache when stale.

    Lock prevents a thundering herd of concurrent refetches when the cache
    expires under load — the first waiter fills it, the rest reuse.
    """
    now = time.time()
    cached: dict[str, dict[str, Any]] = _jwks_cache["keys"]
    if cached and now - _jwks_cache["fetched_at"] < _JWKS_TTL_SECONDS:
        return cached

    async with _jwks_lock:
        now = time.time()
        cached = _jwks_cache["keys"]
        if cached and now - _jwks_cache["fetched_at"] < _JWKS_TTL_SECONDS:
            return cached
        url = _jwks_url(supabase_url)
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(url)
        if resp.status_code >= 400:
            raise PermissionDeniedError(
                "Unable to fetch JWKS",
                error_code="jwks_fetch_failed",
                status=resp.status_code,
            )
        data = resp.json()
        keys: dict[str, dict[str, Any]] = {
            k["kid"]: k for k in data.get("keys", []) if "kid" in k
        }
        _jwks_cache["keys"] = keys
        _jwks_cache["fetched_at"] = time.time()
        logger.info("auth.jwks.refreshed", count=len(keys))
        return keys


def _decode_unverified_header(token: str) -> dict[str, Any]:
    try:
        header_b64 = token.split(".", 1)[0]
        import json

        parsed: dict[str, Any] = json.loads(
            base64url_decode(header_b64.encode()).decode()
        )
        return parsed
    except Exception as e:
        raise PermissionDeniedError(
            "Malformed JWT header", error_code="malformed_token", reason=str(e)
        ) from e


async def _verify_asymmetric(token: str, *, supabase_url: str) -> dict[str, Any]:
    header = _decode_unverified_header(token)
    alg_raw = header.get("alg")
    kid_raw = header.get("kid")
    if not isinstance(alg_raw, str) or not isinstance(kid_raw, str) or not kid_raw:
        raise PermissionDeniedError(
            "Asymmetric JWT missing alg/kid", error_code="missing_kid"
        )
    alg: str = alg_raw
    kid: str = kid_raw

    keys = await _load_jwks(supabase_url)
    key_data = keys.get(kid)
    if key_data is None:
        # Force a fresh fetch in case keys rotated.
        _jwks_cache["fetched_at"] = 0.0
        keys = await _load_jwks(supabase_url)
        key_data = keys.get(kid)
    if key_data is None:
        raise PermissionDeniedError(
            "JWT signed with unknown key", error_code="unknown_kid", kid=kid
        )

    public_key = jwk.construct(key_data, algorithm=alg)
    pem = public_key.to_pem().decode() if hasattr(public_key, "to_pem") else public_key
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            pem,
            algorithms=[alg],
            audience="authenticated",
        )
        return claims
    except JWTError as e:
        raise PermissionDeniedError(
            "Invalid or expired token",
            error_code="invalid_token",
            reason=str(e),
        ) from e


def _verify_symmetric(token: str, *, secret: str) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return claims
    except JWTError as e:
        raise PermissionDeniedError(
            "Invalid or expired token",
            error_code="invalid_token",
            reason=str(e),
        ) from e


async def verify_supabase_jwt(
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise PermissionDeniedError("Missing bearer token", error_code="missing_token")
    token = authorization.split(" ", 1)[1].strip()

    settings = get_settings()
    header = _decode_unverified_header(token)
    alg = str(header.get("alg") or "").upper()

    if alg in ("ES256", "RS256", "ES384", "RS384", "ES512", "RS512"):
        if not settings.supabase_url:
            raise PermissionDeniedError(
                "Server is not configured to verify asymmetric JWTs",
                error_code="jwt_not_configured",
            )
        return await _verify_asymmetric(token, supabase_url=settings.supabase_url)

    if alg == "HS256":
        if not settings.supabase_jwt_secret:
            raise PermissionDeniedError(
                "Server is not configured to verify HS256 JWTs",
                error_code="jwt_not_configured",
            )
        return _verify_symmetric(token, secret=settings.supabase_jwt_secret)

    raise PermissionDeniedError(
        f"JWT algorithm '{alg or 'unknown'}' not allowed",
        error_code="invalid_token",
    )


async def get_tenant_context(
    claims: Annotated[dict[str, Any], Depends(verify_supabase_jwt)],
) -> TenantContext:
    tenant_id = claims.get("tenant_id")
    if tenant_id is None:
        raise PermissionDeniedError("Token missing tenant_id claim", error_code="missing_tenant_claim")
    merchant_id_raw = claims.get("merchant_id")
    role = claims.get("role", "merchant_user")
    actor_id = claims.get("sub")
    if actor_id is None:
        raise PermissionDeniedError("Token missing sub claim", error_code="missing_sub_claim")

    return TenantContext(
        tenant_id=UUID(tenant_id),
        merchant_id=UUID(merchant_id_raw) if merchant_id_raw else None,
        role=str(role),
        actor_id=UUID(actor_id),
    )


def require_role(*allowed: str) -> Any:
    async def _checker(
        ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    ) -> TenantContext:
        if ctx.role not in allowed:
            raise PermissionDeniedError(
                f"Role '{ctx.role}' not allowed",
                error_code="role_not_allowed",
                allowed=list(allowed),
            )
        return ctx

    return _checker
