"""Integration-test harness.

Requires a live Postgres with the schema migrated (CI does this via
`uv run alembic upgrade head` before pytest). The fixtures assume the
connection user can seed cross-tenant rows, which is true for the CI
`postgres` superuser (superusers bypass RLS regardless of FORCE). Locally,
run against `supabase start` or any Postgres you own.

Tests are auto-skipped when `SUPABASE_DB_URL` is unset — unit-only runs stay
fast, and CI is the canonical place these tests light up.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from db import session_scope
from db.models import Merchant, Tenant
from db.session import get_engine


def _dsn() -> str | None:
    return os.environ.get("SUPABASE_DB_URL") or None


@pytest.fixture(scope="session", autouse=True)
def _require_db() -> None:
    if _dsn() is None:
        pytest.skip("SUPABASE_DB_URL not set — skipping integration tests")


@pytest.fixture(scope="session", autouse=True)
def _init_engine(_require_db: None) -> None:
    get_engine(_dsn())


@pytest_asyncio.fixture
async def two_tenants() -> AsyncIterator[tuple[Tenant, Merchant, Tenant, Merchant]]:
    """Seed two disjoint tenants, each with one merchant, then clean up.

    Seeding runs through `session_scope` (no JWT claims set). In CI the DB
    user is a superuser and bypasses RLS; locally the user must own the
    tables or carry BYPASSRLS. We tolerate this because it's a test-only
    dependency.
    """
    suffix = uuid.uuid4().hex[:8]
    t1 = Tenant(slug=f"t1-{suffix}", name=f"Tenant One {suffix}")
    t2 = Tenant(slug=f"t2-{suffix}", name=f"Tenant Two {suffix}")
    async with session_scope() as session:
        session.add_all([t1, t2])
        await session.flush()
        m1 = Merchant(tenant_id=t1.id, slug=f"m1-{suffix}", name=f"Merchant A {suffix}")
        m2 = Merchant(tenant_id=t2.id, slug=f"m2-{suffix}", name=f"Merchant B {suffix}")
        session.add_all([m1, m2])
        await session.flush()
        # Re-read to detach session identity — we hand plain snapshots to tests.
        t1_id, t2_id, m1_id, m2_id = t1.id, t2.id, m1.id, m2.id
        t1_slug, t2_slug, m1_slug, m2_slug = t1.slug, t2.slug, m1.slug, m2.slug
        t1_name, t2_name, m1_name, m2_name = t1.name, t2.name, m1.name, m2.name

    # Snapshot objects detached from any session — tests read fields only.
    snap_t1 = Tenant(id=t1_id, slug=t1_slug, name=t1_name)
    snap_t2 = Tenant(id=t2_id, slug=t2_slug, name=t2_name)
    snap_m1 = Merchant(id=m1_id, tenant_id=t1_id, slug=m1_slug, name=m1_name)
    snap_m2 = Merchant(id=m2_id, tenant_id=t2_id, slug=m2_slug, name=m2_name)

    try:
        yield snap_t1, snap_m1, snap_t2, snap_m2
    finally:
        # Cascade delete via tenants — removes everything we seeded.
        async with session_scope() as session:
            for pk in (t1_id, t2_id):
                row = await session.get(Tenant, pk)
                if row is not None:
                    await session.delete(row)
