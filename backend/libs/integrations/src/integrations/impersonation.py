"""Agency→merchant impersonation token minting (UC-10 / ADR pending).

An `agency_admin` cannot self-serve a merchant Supabase session (GoTrue has no
"create session for arbitrary user" admin call), so we mint a short-lived
HS256 JWT signed with the project's `supabase_jwt_secret`. The token is shaped
to satisfy three readers at once:

1. **FastAPI** (`dependencies/auth.verify_supabase_jwt`) — verifies HS256 with
   `supabase_jwt_secret` and `audience="authenticated"`, then reads the
   *top-level* `tenant_id` / `merchant_id` / `user_role` claims.
2. **web-merchant** (the `(app)/layout.tsx` server component) — reads
   `session.user.app_metadata.{tenant_id,merchant_id}`. Hence the claims are
   duplicated under `app_metadata`.
3. **Supabase data-plane** (PostgREST / Storage / Realtime) — needs the
   top-level `role` claim to stay `"authenticated"` (migration 0007: putting an
   app role there breaks `SET ROLE`). The *app* role lives in `user_role`.

The `act` claim (RFC 8693) records the impersonating admin: it is the marker
`dependencies/auth.get_tenant_context` keys on to flag the session as an
impersonation (audit + agency lock-bypass). There is no valid refresh token —
the session is deliberately non-renewable and expires at `exp`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from jose import jwt

# Clamp the caller-supplied TTL into a sane window: long enough for a real
# config session, short enough that a stateless (non-revocable) token is low
# risk.
_MIN_TTL_SECONDS = 300  # 5 min
_MAX_TTL_SECONDS = 1800  # 30 min


@dataclass(slots=True, frozen=True)
class ImpersonationToken:
    access_token: str
    expires_at: int  # epoch seconds (UTC)
    expires_in: int  # seconds from now
    session_id: UUID
    merchant_id: UUID
    tenant_id: UUID


def mint_impersonation_token(
    *,
    jwt_secret: str,
    supabase_url: str,
    admin_user_id: UUID,
    admin_email: str | None,
    tenant_id: UUID,
    merchant_id: UUID,
    merchant_name: str,
    ttl_seconds: int = 1200,
    app_role: str = "merchant_user",
) -> ImpersonationToken:
    """Mint a merchant-scoped HS256 access token on behalf of an agency admin.

    `jwt_secret` must be the project's `supabase_jwt_secret`. Raises
    `ValueError` if it is empty (otherwise the token would be unverifiable).
    """
    if not jwt_secret:
        raise ValueError("supabase_jwt_secret is not configured; cannot mint token")

    ttl = max(_MIN_TTL_SECONDS, min(int(ttl_seconds), _MAX_TTL_SECONDS))
    now = datetime.now(tz=UTC)
    iat = int(now.timestamp())
    exp = iat + ttl
    session_id = uuid4()

    tenant_s = str(tenant_id)
    merchant_s = str(merchant_id)

    act: dict[str, str] = {"sub": str(admin_user_id), "type": "impersonation"}
    if admin_email:
        act["email"] = admin_email

    payload: dict[str, object] = {
        "iss": f"{supabase_url.rstrip('/')}/auth/v1" if supabase_url else "reloop-impersonation",
        "aud": "authenticated",
        "sub": str(admin_user_id),
        "iat": iat,
        "exp": exp,
        # Postgres role for PostgREST — NOT the app role (see module docstring).
        "role": "authenticated",
        # App role read by FastAPI's get_tenant_context.
        "user_role": app_role,
        # Top-level claims for the backend + RLS.
        "tenant_id": tenant_s,
        "merchant_id": merchant_s,
        # app_metadata for the web-merchant layout.
        "app_metadata": {
            "tenant_id": tenant_s,
            "merchant_id": merchant_s,
            "role": app_role,
            "provider": "impersonation",
        },
        "user_metadata": {"impersonated_merchant_name": merchant_name},
        # Impersonation marker (audit + lock bypass).
        "act": act,
        "session_id": str(session_id),
    }

    token = jwt.encode(payload, jwt_secret, algorithm="HS256")
    return ImpersonationToken(
        access_token=token,
        expires_at=exp,
        expires_in=ttl,
        session_id=session_id,
        merchant_id=merchant_id,
        tenant_id=tenant_id,
    )
