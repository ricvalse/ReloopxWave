"""Cross-tenant RLS isolation for the appointments mirror (UC-02).

Mirrors test_isolation_catalog.py: two JWT-shaped contexts against a real
Postgres, asserting tenant A cannot observe tenant B's appointment rows through
a repository list, a raw SELECT, or a PK lookup. Auto-skips when SUPABASE_DB_URL
is unset.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db import (
    AppointmentRepository,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.models import Appointment, Merchant, Tenant

pytestmark = pytest.mark.asyncio

TwoTenants = tuple[Tenant, Merchant, Tenant, Merchant]

_START = datetime(2026, 7, 15, 15, 0, tzinfo=UTC)


async def _seed_appointment(merchant_id: uuid.UUID, ghl_event_id: str) -> uuid.UUID:
    async with session_scope() as session:
        appt = Appointment(
            merchant_id=merchant_id,
            ghl_appointment_id=ghl_event_id,
            calendar_id="CAL-1",
            start_at=_START,
            tz_name="Europe/Rome",
        )
        session.add(appt)
        await session.flush()
        return appt.id


async def test_tenant_cannot_read_other_tenant_appointments(two_tenants: TwoTenants) -> None:
    t1, m1, _t2, m2 = two_tenants
    await _seed_appointment(m1.id, f"evt-a-{uuid.uuid4().hex[:8]}")
    foreign_id = await _seed_appointment(m2.id, f"evt-b-{uuid.uuid4().hex[:8]}")

    async with tenant_session(_admin_ctx(t1.id)) as session:
        repo = AppointmentRepository(session)
        assert len(await repo.list_for_merchant(m1.id)) == 1
        assert await repo.list_for_merchant(m2.id) == []

        rows = (await session.execute(select(Appointment))).scalars().all()
        assert all(r.merchant_id == m1.id for r in rows)

        # PK lookup for the foreign row returns None under FORCE RLS.
        assert await repo.get(foreign_id) is None


async def test_merchant_user_pinned_to_own_appointments(two_tenants: TwoTenants) -> None:
    """A merchant-scoped JWT (merchant_id claim set) sees only its own rows even
    within the same tenant — the WITH CHECK / USING predicate pins on m.id."""
    t1, m1, _t2, _m2 = two_tenants
    await _seed_appointment(m1.id, f"evt-{uuid.uuid4().hex[:8]}")

    async with tenant_session(_merchant_ctx(t1.id, m1.id)) as session:
        repo = AppointmentRepository(session)
        assert len(await repo.list_for_merchant(m1.id)) == 1


async def test_upsert_by_ghl_id_is_idempotent(two_tenants: TwoTenants) -> None:
    """The reconcile poll re-runs every 30 min: upserting the same GHL event id
    must update the existing row (time/status), never duplicate it."""
    t1, m1, _t2, _m2 = two_tenants
    evt = f"evt-{uuid.uuid4().hex[:8]}"

    async with tenant_session(_admin_ctx(t1.id)) as session:
        repo = AppointmentRepository(session)
        await repo.upsert_by_ghl_id(
            merchant_id=m1.id, ghl_appointment_id=evt, start_at=_START, end_at=None
        )
        # Same event, rescheduled +1h and cancelled in GHL → reflected, not dupes.
        await repo.upsert_by_ghl_id(
            merchant_id=m1.id,
            ghl_appointment_id=evt,
            start_at=_START + timedelta(hours=1),
            end_at=None,
            status="cancelled",
        )
        rows = await repo.list_for_merchant(m1.id)

    assert len(rows) == 1
    assert rows[0].status == "cancelled"
    assert rows[0].start_at == _START + timedelta(hours=1)


def _admin_ctx(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        merchant_id=None,
        role="agency_admin",
        actor_id=uuid.uuid4(),
    )


def _merchant_ctx(tenant_id: uuid.UUID, merchant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        role="merchant_admin",
        actor_id=uuid.uuid4(),
    )
