"""UC-07 — apertura di un documento della KB (signed URL + testo indicizzato).

Stubba sessione/repo/Storage così non serve né Postgres né Supabase: quello che
ci interessa verificare è il contratto di scoping. In particolare il **guard
IDOR**: l'URL è firmato con la service role, che bypassa la RLS del bucket, per
cui l'unica cosa che tiene separati due merchant è il check sul prefisso del
path — se salta, un merchant scarica i file di un altro.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
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
from shared import IntegrationError

_MERCHANT_ID = uuid4()
_TENANT_ID = uuid4()
_OTHER_MERCHANT_ID = uuid4()


# --- repository contract -------------------------------------------------


@dataclass
class _FakeDoc:
    merchant_id: UUID


class _FakeSession:
    def __init__(self, doc: _FakeDoc | None) -> None:
        self._doc = doc

    async def get(self, _model: object, _doc_id: UUID) -> _FakeDoc | None:
        return self._doc


@pytest.mark.asyncio
async def test_get_for_merchant_returns_doc_when_owned() -> None:
    doc = _FakeDoc(merchant_id=_MERCHANT_ID)
    repo = KnowledgeBaseRepository(_FakeSession(doc))  # type: ignore[arg-type]

    assert await repo.get_for_merchant(_MERCHANT_ID, uuid4()) is doc


@pytest.mark.asyncio
async def test_get_for_merchant_returns_none_when_missing() -> None:
    repo = KnowledgeBaseRepository(_FakeSession(None))  # type: ignore[arg-type]

    assert await repo.get_for_merchant(_MERCHANT_ID, uuid4()) is None


@pytest.mark.asyncio
async def test_get_for_merchant_returns_none_for_other_merchant() -> None:
    doc = _FakeDoc(merchant_id=_OTHER_MERCHANT_ID)
    repo = KnowledgeBaseRepository(_FakeSession(doc))  # type: ignore[arg-type]

    assert await repo.get_for_merchant(_MERCHANT_ID, uuid4()) is None


# --- endpoints -----------------------------------------------------------


@dataclass
class _Doc:
    id: UUID
    merchant_id: UUID
    source: str
    storage_path: str | None = None
    url: str | None = None


@dataclass
class _Chunk:
    id: UUID
    chunk_index: int
    content: str
    tokens: int


@dataclass
class _StorageCalls:
    signed: list[tuple[str, int]] = field(default_factory=list)


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    doc: _Doc | None,
    chunks: list[_Chunk] | None = None,
    merchant_id: UUID | None = _MERCHANT_ID,
    sign_raises: bool = False,
) -> tuple[TestClient, _StorageCalls]:
    calls = _StorageCalls()
    seen_limits: list[tuple[int, int]] = []

    class _StubRepo:
        def __init__(self, _session: object) -> None:
            pass

        async def get_for_merchant(self, mid: UUID, _doc_id: UUID) -> _Doc | None:
            if doc is None or doc.merchant_id != mid:
                return None
            return doc

        async def list_chunks(
            self, _mid: UUID, _doc_id: UUID, *, limit: int, offset: int
        ) -> list[_Chunk]:
            seen_limits.append((limit, offset))
            return (chunks or [])[offset : offset + limit]

    class _StubStorage:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def create_signed_url(self, path: str, *, expires_in_seconds: int) -> str:
            if sign_raises:
                raise IntegrationError("nope", error_code="storage_sign_failed")
            calls.signed.append((path, expires_in_seconds))
            return f"https://storage.example/{path}?token=abc"

    class _StubSettings:
        supabase_url = "https://proj.supabase.co"
        supabase_service_role_key = "service-role"
        supabase_kb_bucket = "kb-documents"

    monkeypatch.setattr(knowledge_base, "KnowledgeBaseRepository", _StubRepo)
    monkeypatch.setattr(knowledge_base, "SupabaseStorage", _StubStorage)
    monkeypatch.setattr(knowledge_base, "get_settings", lambda: _StubSettings())

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(knowledge_base.router, prefix="/knowledge-base")

    async def _ctx() -> TenantContext:
        return TenantContext(
            tenant_id=_TENANT_ID,
            merchant_id=merchant_id,
            role="merchant_admin",
            actor_id=_TENANT_ID,
        )

    async def _session() -> object:
        return object()

    app.dependency_overrides[get_tenant_context] = _ctx
    app.dependency_overrides[get_db_session] = _session
    client = TestClient(app)
    client.seen_limits = seen_limits  # type: ignore[attr-defined]
    return client, calls


def _view_url(doc_id: UUID, merchant_id: UUID = _MERCHANT_ID) -> str:
    return f"/knowledge-base/{merchant_id}/docs/{doc_id}/view"


def test_view_signs_url_for_uploaded_file(monkeypatch: pytest.MonkeyPatch) -> None:
    doc_id = uuid4()
    doc = _Doc(
        id=doc_id,
        merchant_id=_MERCHANT_ID,
        source="pdf",
        storage_path=f"{_MERCHANT_ID}/1700000000-listino.pdf",
    )
    client, calls = _make_client(monkeypatch, doc=doc)

    resp = client.get(_view_url(doc_id))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "file"
    assert body["url"].startswith("https://storage.example/")
    assert body["mime"] == "application/pdf"
    assert body["filename"] == "1700000000-listino.pdf"
    assert body["expires_at"] is not None
    assert calls.signed == [(doc.storage_path, 3600)]


def test_view_returns_external_link_for_url_doc(monkeypatch: pytest.MonkeyPatch) -> None:
    doc_id = uuid4()
    doc = _Doc(id=doc_id, merchant_id=_MERCHANT_ID, source="url", url="https://esempio.it/listino")
    client, calls = _make_client(monkeypatch, doc=doc)

    resp = client.get(_view_url(doc_id))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "kind": "url",
        "url": "https://esempio.it/listino",
        "mime": None,
        "filename": None,
        "expires_at": None,
    }
    assert calls.signed == []  # nessuna firma per i doc da URL


def test_view_404_when_doc_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _make_client(monkeypatch, doc=None)

    resp = client.get(_view_url(uuid4()))

    assert resp.status_code == 404, resp.text


def test_view_404_when_doc_has_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Corpus sintetici (FAQ/catalogo): esistono solo come chunk."""
    doc_id = uuid4()
    doc = _Doc(id=doc_id, merchant_id=_MERCHANT_ID, source="faq", storage_path=None)
    client, _ = _make_client(monkeypatch, doc=doc)

    resp = client.get(_view_url(doc_id))

    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "kb_doc_has_no_file"


def test_view_403_when_storage_path_belongs_to_another_merchant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il guard IDOR: `storage_path` è scritto da input utente su POST /docs.

    La service role bypassa la RLS del bucket, quindi senza questo check un doc
    con un path forgiato farebbe firmare il file di un altro merchant.
    """
    doc_id = uuid4()
    doc = _Doc(
        id=doc_id,
        merchant_id=_MERCHANT_ID,
        source="pdf",
        storage_path=f"{_OTHER_MERCHANT_ID}/segreti.pdf",
    )
    client, calls = _make_client(monkeypatch, doc=doc)

    resp = client.get(_view_url(doc_id))

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "kb_doc_cross_merchant"
    assert calls.signed == []


def test_view_403_without_merchant_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token di agenzia senza claim merchant: `_assert_merchant_scope` passerebbe."""
    doc_id = uuid4()
    doc = _Doc(
        id=doc_id,
        merchant_id=_MERCHANT_ID,
        source="pdf",
        storage_path=f"{_MERCHANT_ID}/listino.pdf",
    )
    client, calls = _make_client(monkeypatch, doc=doc, merchant_id=None)

    resp = client.get(_view_url(doc_id))

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "no_merchant_context"
    assert calls.signed == []


def test_view_403_cross_merchant_path_param(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _Doc(id=uuid4(), merchant_id=_MERCHANT_ID, source="pdf", storage_path="x/y.pdf")
    client, _ = _make_client(monkeypatch, doc=doc)

    resp = client.get(_view_url(uuid4(), merchant_id=_OTHER_MERCHANT_ID))

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "cross_merchant_access"


def test_view_404_when_object_gone_from_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Storage risponde 4xx sul sign se l'oggetto non c'è: per il merchant è 404."""
    doc_id = uuid4()
    doc = _Doc(
        id=doc_id,
        merchant_id=_MERCHANT_ID,
        source="pdf",
        storage_path=f"{_MERCHANT_ID}/sparito.pdf",
    )
    client, _ = _make_client(monkeypatch, doc=doc, sign_raises=True)

    resp = client.get(_view_url(doc_id))

    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "kb_file_unavailable"


# --- chunks --------------------------------------------------------------


def _chunks_url(doc_id: UUID, merchant_id: UUID = _MERCHANT_ID) -> str:
    return f"/knowledge-base/{merchant_id}/docs/{doc_id}/chunks"


def test_chunks_returns_indexed_text_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    doc_id = uuid4()
    doc = _Doc(id=doc_id, merchant_id=_MERCHANT_ID, source="pdf", storage_path="p")
    chunks = [
        _Chunk(id=uuid4(), chunk_index=0, content="primo", tokens=3),
        _Chunk(id=uuid4(), chunk_index=1, content="secondo", tokens=4),
    ]
    client, _ = _make_client(monkeypatch, doc=doc, chunks=chunks)

    resp = client.get(_chunks_url(doc_id))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [c["chunk_index"] for c in body] == [0, 1]
    assert [c["content"] for c in body] == ["primo", "secondo"]
    assert body[0]["tokens"] == 3


def test_chunks_404_when_doc_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _make_client(monkeypatch, doc=None)

    resp = client.get(_chunks_url(uuid4()))

    assert resp.status_code == 404, resp.text


def test_chunks_403_cross_merchant(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _Doc(id=uuid4(), merchant_id=_MERCHANT_ID, source="pdf", storage_path="p")
    client, _ = _make_client(monkeypatch, doc=doc)

    resp = client.get(_chunks_url(uuid4(), merchant_id=_OTHER_MERCHANT_ID))

    assert resp.status_code == 403, resp.text


def test_chunks_rejects_out_of_range_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bound dichiarati nello schema, non clampati in silenzio.

    Un client che chiede 500 e ne riceve 200 senza saperlo avanzerebbe l'offset
    di 500, saltando 300 chunk: meglio un 422 esplicito.
    """
    doc_id = uuid4()
    doc = _Doc(id=doc_id, merchant_id=_MERCHANT_ID, source="pdf", storage_path=f"{_MERCHANT_ID}/p")
    client, _ = _make_client(monkeypatch, doc=doc, chunks=[])

    assert client.get(_chunks_url(doc_id), params={"limit": 10_000}).status_code == 422
    assert client.get(_chunks_url(doc_id), params={"limit": 0}).status_code == 422
    assert client.get(_chunks_url(doc_id), params={"offset": -5}).status_code == 422
    assert client.get(_chunks_url(doc_id), params={"limit": 200, "offset": 0}).status_code == 200

    assert client.seen_limits == [(200, 0)]  # type: ignore[attr-defined]


# --- path traversal ------------------------------------------------------


@pytest.mark.parametrize(
    "forged",
    [
        pytest.param("{mid}/../{other}/listino.pdf", id="dot-dot-cross-merchant"),
        pytest.param("{mid}/../../whatsapp-media/{other}/foto.jpg", id="dot-dot-cross-bucket"),
        pytest.param("{mid}/./../{other}/x.pdf", id="dot-then-dot-dot"),
        pytest.param("{mid}//../{other}/x.pdf", id="double-slash"),
        pytest.param("/{mid}/x.pdf", id="absolute"),
        pytest.param("{mid}/..%2F{other}/x.pdf", id="percent-encoded"),
        pytest.param("{mid}\\..\\{other}\\x.pdf", id="backslash"),
        pytest.param("{other}/x.pdf", id="plain-cross-merchant"),
        pytest.param("{mid}", id="no-object-segment"),
    ],
)
def test_view_403_on_forged_storage_path(monkeypatch: pytest.MonkeyPatch, forged: str) -> None:
    """httpx normalizza i dot-segment PRIMA di spedire.

    Con un check sul solo primo segmento, `{mid}/../{altro}/f.pdf` verrebbe
    firmato come `{altro}/f.pdf` — e con due `..` si esce dal bucket. Siccome la
    firma usa la service role (RLS del bucket bypassata), qui non c'è nient'altro
    a fermare la fuga.
    """
    doc_id = uuid4()
    path = forged.format(mid=_MERCHANT_ID, other=_OTHER_MERCHANT_ID)
    doc = _Doc(id=doc_id, merchant_id=_MERCHANT_ID, source="pdf", storage_path=path)
    client, calls = _make_client(monkeypatch, doc=doc)

    resp = client.get(_view_url(doc_id))

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "kb_doc_cross_merchant"
    assert calls.signed == []  # non deve arrivare mai a Storage


@dataclass
class _CreatedDoc:
    id: UUID
    title: str
    source: str
    status: str = "pending"
    chunk_count: int = 0
    status_detail: str | None = None
    last_error: str | None = None


def _make_create_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, list[str | None]]:
    """Client per POST /docs, con arq finto (il router accoda kb_reindex)."""
    persisted: list[str | None] = []

    class _StubRepo:
        def __init__(self, _session: object) -> None:
            pass

        async def create_doc(self, **kw: Any) -> _CreatedDoc:
            persisted.append(kw.get("storage_path"))
            return _CreatedDoc(id=uuid4(), title=kw["title"], source=kw["source"])

    class _FakeArq:
        async def enqueue_job(self, *_a: Any, **_kw: Any) -> None:
            return None

    monkeypatch.setattr(knowledge_base, "KnowledgeBaseRepository", _StubRepo)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(knowledge_base.router, prefix="/knowledge-base")
    app.state.arq = _FakeArq()

    async def _ctx() -> TenantContext:
        return TenantContext(
            tenant_id=_TENANT_ID,
            merchant_id=_MERCHANT_ID,
            role="merchant_admin",
            actor_id=_TENANT_ID,
        )

    async def _session() -> object:
        return object()

    app.dependency_overrides[get_tenant_context] = _ctx
    app.dependency_overrides[get_db_session] = _session
    return TestClient(app), persisted


def test_create_doc_rejects_forged_storage_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """La radice: se il path avvelenato entra in DB, `kb_reindex` lo scarica.

    Il worker fa `storage.download(storage_path)` con la service role e senza
    guard: un path forgiato gli fa indicizzare il documento di un altro merchant
    dentro la KB dell'attaccante, da dove è poi leggibile in chiaro.
    """
    client, persisted = _make_create_client(monkeypatch)

    resp = client.post(
        f"/knowledge-base/{_MERCHANT_ID}/docs",
        json={
            "title": "x",
            "source": "pdf",
            "storage_path": f"{_MERCHANT_ID}/../{_OTHER_MERCHANT_ID}/listino.pdf",
        },
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "kb_invalid_storage_path"
    assert persisted == []


def test_create_doc_accepts_own_storage_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client, persisted = _make_create_client(monkeypatch)
    path = f"{_MERCHANT_ID}/1700000000-listino.pdf"

    resp = client.post(
        f"/knowledge-base/{_MERCHANT_ID}/docs",
        json={"title": "Listino", "source": "pdf", "storage_path": path},
    )

    assert resp.status_code == 200, resp.text
    assert persisted == [path]


def test_create_doc_still_accepts_url_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    """I doc da URL non hanno storage_path: la validazione non deve bloccarli."""
    client, persisted = _make_create_client(monkeypatch)

    resp = client.post(
        f"/knowledge-base/{_MERCHANT_ID}/docs",
        json={"title": "Sito", "source": "url", "url": "https://esempio.it"},
    )

    assert resp.status_code == 200, resp.text
    assert persisted == [None]


def test_view_accepts_nested_but_in_scope_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """La validazione non deve rompere i path legittimi (anche annidati)."""
    doc_id = uuid4()
    path = f"{_MERCHANT_ID}/2026/1700000000-listino.pdf"
    doc = _Doc(id=doc_id, merchant_id=_MERCHANT_ID, source="pdf", storage_path=path)
    client, calls = _make_client(monkeypatch, doc=doc)

    assert client.get(_view_url(doc_id)).status_code == 200
    assert calls.signed == [(path, 3600)]
