"""JWT verification + tenant-context extraction.

Supabase signs JWTs with a shared HMAC secret (project JWT secret). In V1 we
verify with the secret directly; a JWKS fetch can be swapped in later if we
move to asymmetric signing. The custom claims `tenant_id`, `merchant_id`, and
`role` are written by a Supabase Auth hook and passed through here.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from jose import JWTError, jwt

from db.session import TenantContext
from shared import PermissionDeniedError, get_settings


async def verify_supabase_jwt(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise PermissionDeniedError("Missing bearer token", error_code="missing_token")
    token = authorization.split(" ", 1)[1].strip()
    settings = get_settings()
    if not settings.supabase_jwt_secret:
        raise PermissionDeniedError(
            "Server is not configured to verify JWTs",
            error_code="jwt_not_configured",
        )
    try:
        claims = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except JWTError as e:
        raise PermissionDeniedError(
            "Invalid or expired token",
            error_code="invalid_token",
            reason=str(e),
        ) from e
    return claims


async def get_tenant_context(
    claims: Annotated[dict, Depends(verify_supabase_jwt)],
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


def require_role(*allowed: str):
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
