from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

# Postgres role assumed inside `tenant_session` so RLS is actually enforced.
#
# Our RLS policies are FORCEd, but FORCE ROW LEVEL SECURITY is *silently
# ignored* for roles with the BYPASSRLS attribute — and Supabase's default
# `postgres` login role (used by the pooled connection in production) has
# BYPASSRLS. Without downgrading, every `tenant_isolation_*` policy is bypassed
# and the whole platform leaks cross-tenant. `authenticated` is the standard
# Supabase role (NOBYPASSRLS) the policies are written against, and `postgres`
# is a member of it, so `SET LOCAL ROLE authenticated` always succeeds and
# reverts automatically at transaction end. Override for non-Supabase
# environments / tests via `set_rls_tenant_role`.
_RLS_TENANT_ROLE: str | None = "authenticated"
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def set_rls_tenant_role(role: str | None) -> None:
    """Override the Postgres role assumed inside `tenant_session`.

    Pass a role name (validated as a plain SQL identifier) to downgrade to it
    for every tenant-scoped transaction; pass `None`/`""` to disable the
    `SET LOCAL ROLE` (only safe when the login role is a non-superuser,
    NOBYPASSRLS table owner relying on FORCE RLS).
    """
    global _RLS_TENANT_ROLE
    if role and not _IDENT_RE.match(role):
        raise ValueError(f"Invalid RLS role identifier: {role!r}")
    _RLS_TENANT_ROLE = role or None


@dataclass(slots=True, frozen=True)
class TenantContext:
    """Claims extracted from a Supabase JWT used to scope DB access.

    Applied via `SET LOCAL` so Postgres RLS policies see the tenant on every
    query within the session's transaction.

    `impersonator_id` is set only when the JWT is an agency→merchant
    impersonation token (carries an `act` claim, see
    `integrations.impersonation`). In that case `actor_id`/`role`/`merchant_id`
    describe the *impersonated merchant* (so RLS and the app behave exactly as
    the merchant would), while `impersonator_id` records the agency admin who
    minted the session — used for audit and to let the agency bypass its own
    `locked_keys` (UC-10).
    """

    tenant_id: UUID
    merchant_id: UUID | None
    role: str
    actor_id: UUID
    impersonator_id: UUID | None = None
    impersonation_session_id: UUID | None = None

    @property
    def is_impersonation(self) -> bool:
        return self.impersonator_id is not None


def create_engine(dsn: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(
        dsn,
        echo=echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def get_engine(dsn: str | None = None) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        if dsn is None:
            raise RuntimeError("Engine not initialised; provide DSN on first call.")
        _engine = create_engine(dsn)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Call get_engine(dsn) first to initialise the session factory.")
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def tenant_session(ctx: TenantContext) -> AsyncIterator[AsyncSession]:
    """Open a session that sets `request.jwt.claims` for RLS.

    Supabase RLS policies read from `auth.jwt() ->> 'tenant_id'` etc. For
    backend-initiated sessions that don't carry a live JWT, we forge the same
    claim surface via `SET LOCAL "request.jwt.claims"`. The JSON is interpreted
    by Supabase's `auth.jwt()` helper.
    """
    factory = get_session_factory()
    async with factory() as session:
        claims: dict[str, object] = {
            "tenant_id": str(ctx.tenant_id),
            "merchant_id": str(ctx.merchant_id) if ctx.merchant_id else None,
            "user_role": ctx.role,
            "sub": str(ctx.actor_id),
        }
        # Downgrade from the (possibly BYPASSRLS) login role to a role the RLS
        # policies actually apply to. `SET LOCAL` scopes it to this transaction
        # and reverts on commit/rollback, so the pooled connection is clean for
        # the next checkout. The role name is a validated identifier (see
        # `set_rls_tenant_role`), so the f-string interpolation is injection-safe;
        # `SET ROLE` accepts no bind parameters. Must run inside the transaction
        # SQLAlchemy autobegins on this first execute.
        if _RLS_TENANT_ROLE is not None:
            await session.execute(text(f'SET LOCAL ROLE "{_RLS_TENANT_ROLE}"'))
        # Postgres does not accept bind parameters on the `SET LOCAL ...`
        # command — the value has to be a literal. `set_config()` is the
        # function equivalent and does take parameters, so we use that to
        # avoid string-interpolating JSON into SQL. `is_local=true` scopes
        # the setting to this transaction, same as SET LOCAL would.
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": _json_dump(claims)},
        )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _json_dump(claims: dict[str, object]) -> str:
    import json

    return json.dumps(claims, separators=(",", ":"))
