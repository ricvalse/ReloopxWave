"""UC-07 — KB doc deletion (repository + DELETE endpoint).

Stubs the DB session/repo so no Postgres is needed: we only verify the
repository's merchant-scoping contract and that the endpoint maps a missing /
cross-merchant doc to 404 and a successful delete to 204.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.core.errors import register_exception_handlers
from api.dependencies.auth import get_tenant_context
from api.dependencies.session import get_db_session
from api.routers import knowledge_base
from db.repositories.kb import KnowledgeBaseRepository
from db.session import TenantContext

# --- repository contract -------------------------------------------------


@dataclass
class _FakeDoc:
    merchant_id: UUID


class _FakeSession:
    def __init__(self, doc: _FakeDoc | None) -> None:
        self._doc = doc
        self.deleted: list[object] = []
        self.flushed = False

    async def get(self, _model: object, _doc_id: UUID) -> _FakeDoc | None:
        return self._doc

    async def delete(self, obj: object) -> None:
        self.deleted.append(obj)

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.asyncio
async def test_delete_doc_returns_true_and_deletes_when_owned() -> None:
    merchant_id = uuid4()
    doc = _FakeDoc(merchant_id=merchant_id)
    session = _FakeSession(doc)
    repo = KnowledgeBaseRepository(session)  # type: ignore[arg-type]

    ok = await repo.delete_doc(merchant_id, uuid4())

    assert ok is True
    assert session.deleted == [doc]
    assert session.flushed is True


@pytest.mark.asyncio
async def test_delete_doc_returns_false_when_missing() -> None:
    session = _FakeSession(None)
    repo = KnowledgeBaseRepository(session)  # type: ignore[arg-type]

    ok = await repo.delete_doc(uuid4(), uuid4())

    assert ok is False
    assert session.deleted == []


@pytest.mark.asyncio
async def test_delete_doc_returns_false_for_other_merchant() -> None:
    doc = _FakeDoc(merchant_id=uuid4())
    session = _FakeSession(doc)
    repo = KnowledgeBaseRepository(session)  # type: ignore[arg-type]

    ok = await repo.delete_doc(uuid4(), uuid4())  # different merchant

    assert ok is False
    assert session.deleted == []


# --- endpoint ------------------------------------------------------------

_MERCHANT_ID = uuid4()
_TENANT_ID = uuid4()


def _make_client(delete_result: bool) -> TestClient:
    class _StubRepo:
        def __init__(self, _session: object) -> None:
            pass

        async def delete_doc(self, merchant_id: UUID, doc_id: UUID) -> bool:
            return delete_result

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(knowledge_base.router, prefix="/knowledge-base")

    async def _ctx() -> TenantContext:
        return TenantContext(
            tenant_id=_TENANT_ID,
            merchant_id=_MERCHANT_ID,
            role="merchant_admin",
            actor_id=_MERCHANT_ID,
        )

    async def _session() -> object:
        return object()

    app.dependency_overrides[get_tenant_context] = _ctx
    app.dependency_overrides[get_db_session] = _session
    knowledge_base.KnowledgeBaseRepository = _StubRepo  # type: ignore[misc,assignment]
    return TestClient(app)


def test_delete_endpoint_204_on_success() -> None:
    client = _make_client(delete_result=True)
    resp = client.delete(f"/knowledge-base/{_MERCHANT_ID}/docs/{uuid4()}")
    assert resp.status_code == 204, resp.text


def test_delete_endpoint_404_when_missing() -> None:
    client = _make_client(delete_result=False)
    resp = client.delete(f"/knowledge-base/{_MERCHANT_ID}/docs/{uuid4()}")
    assert resp.status_code == 404, resp.text


def test_delete_endpoint_403_cross_merchant() -> None:
    client = _make_client(delete_result=True)
    resp = client.delete(f"/knowledge-base/{uuid4()}/docs/{uuid4()}")
    assert resp.status_code == 403, resp.text
