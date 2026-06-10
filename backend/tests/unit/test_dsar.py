"""DSAR endpoints: export returns the lead + conversations + messages; erase
deletes conversations and strips PII. Both reject callers without merchant scope.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api.routers.dsar import erase_lead, export_lead
from shared import PermissionDeniedError


class FakeResult:
    def __init__(self, *, scalar=None, items=None, rowcount=None):
        self._scalar = scalar
        self._items = items or []
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._items


class FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.committed = False

    async def execute(self, *a, **k):
        return self._results.pop(0)

    async def commit(self):
        self.committed = True


def _ctx():
    return SimpleNamespace(merchant_id=uuid4(), role="merchant_admin", actor_id=uuid4())


def _lead():
    return SimpleNamespace(
        id=uuid4(),
        merchant_id=uuid4(),
        phone="39333000000",
        name="Mario Rossi",
        email="m@example.com",
        ghl_contact_id="CT-1",
        score=80,
        sentiment="positive",
        status="qualified",
        meta={"k": "v"},
    )


async def test_export_returns_lead_conversations_messages() -> None:
    lead = _lead()
    conv = SimpleNamespace(
        id=uuid4(),
        wa_contact_phone="39333000000",
        status="active",
        started_at=datetime(2026, 6, 1, tzinfo=UTC),
        last_message_at=datetime(2026, 6, 2, tzinfo=UTC),
    )
    msg = SimpleNamespace(
        id=uuid4(),
        conversation_id=conv.id,
        role="user",
        direction="in",
        content="ciao",
        created_at=datetime(2026, 6, 1, 10, tzinfo=UTC),
    )
    session = FakeSession(
        [FakeResult(scalar=lead), FakeResult(items=[conv]), FakeResult(items=[msg])]
    )

    out = await export_lead(lead.id, _ctx(), session)

    assert out["lead"]["name"] == "Mario Rossi"
    assert len(out["conversations"]) == 1
    assert out["messages"][0]["content"] == "ciao"


async def test_erase_deletes_conversations_and_strips_pii() -> None:
    lead = _lead()
    session = FakeSession([FakeResult(scalar=lead), FakeResult(rowcount=3)])

    out = await erase_lead(lead.id, _ctx(), session)

    assert out["erased"] is True
    assert out["conversations_deleted"] == 3
    assert lead.name is None
    assert lead.email is None
    assert lead.ghl_contact_id is None
    assert lead.phone == f"erased:{lead.id}"
    assert lead.status == "erased"
    assert lead.meta["erased"] is True
    assert session.committed is True


async def test_export_404_when_missing() -> None:
    from fastapi import HTTPException

    session = FakeSession([FakeResult(scalar=None)])
    with pytest.raises(HTTPException):
        await export_lead(uuid4(), _ctx(), session)


async def test_requires_merchant_scope() -> None:
    session = FakeSession([FakeResult(scalar=_lead())])
    bad_ctx = SimpleNamespace(merchant_id=None, role="viewer", actor_id=uuid4())
    with pytest.raises(PermissionDeniedError):
        await erase_lead(uuid4(), bad_ctx, session)
