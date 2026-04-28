from __future__ import annotations

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


@dataclass(slots=True, frozen=True)
class TenantContext:
    """Claims extracted from a Supabase JWT used to scope DB access.

    Applied via `SET LOCAL` so Postgres RLS policies see the tenant on every
    query within the session's transaction.
    """

    tenant_id: UUID
    merchant_id: UUID | None
    role: str
    actor_id: UUID


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
        claims = {
            "tenant_id": str(ctx.tenant_id),
            "merchant_id": str(ctx.merchant_id) if ctx.merchant_id else None,
            "user_role": ctx.role,
            "sub": str(ctx.actor_id),
        }
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
