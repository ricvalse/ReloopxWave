"""UC-13 — objection classifier honors the configured category vocabulary and
the ObjectionRepository applies the `bot_variant` filter on its queries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from ai_core import ChatMessage, ObjectionClassifierInput, classify_objections
from db.repositories.objection import ObjectionRepository


@dataclass
class _Result:
    content: str
    model: str = "gpt-5-mini"
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


class _CaptureClient:
    """Fake LLM client: records the system prompt and echoes a canned reply."""

    model = "gpt-5-mini"

    def __init__(self, reply: dict) -> None:
        self._reply = reply
        self.system_prompt: str = ""

    async def complete(self, *, messages, response_format=None, temperature=None, max_tokens=None):
        self.system_prompt = next(m.content for m in messages if m.role == "system")
        return _Result(content=json.dumps(self._reply))


async def test_classifier_uses_configured_categories_in_prompt() -> None:
    custom = ["budget", "tempistiche", "altro"]
    client = _CaptureClient(
        {"objections": [{"category": "budget", "severity": "high", "summary": "costa troppo"}]}
    )
    payload = ObjectionClassifierInput(
        conversation_id="c1",
        transcript=[ChatMessage(role="user", content="è troppo caro")],
        categories=custom,
    )
    result = await classify_objections(client, payload=payload)  # type: ignore[arg-type]

    # The configured vocabulary must reach the prompt.
    assert "budget" in client.system_prompt
    assert "tempistiche" in client.system_prompt
    # And it must NOT leak the hardcoded default taxonomy.
    assert "prezzo" not in client.system_prompt
    assert [o.category for o in result] == ["budget"]


async def test_classifier_drops_categories_outside_configured_set() -> None:
    client = _CaptureClient(
        {
            "objections": [
                {"category": "budget", "severity": "low", "summary": "ok"},
                {"category": "prezzo", "severity": "low", "summary": "fuori vocabolario"},
            ]
        }
    )
    payload = ObjectionClassifierInput(
        conversation_id="c1",
        transcript=[ChatMessage(role="user", content="mmm")],
        categories=["budget", "altro"],
    )
    result = await classify_objections(client, payload=payload)  # type: ignore[arg-type]
    assert [o.category for o in result] == ["budget"]


# --- Repository: bot_variant filter is compiled into the WHERE clause ---


class _CapturingSession:
    """Captures the SQL statements executed so we can assert on the compiled WHERE."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, stmt):
        self.statements.append(str(stmt.compile(compile_kwargs={"literal_binds": False})))
        return _EmptyResult()


class _EmptyResult:
    def all(self):
        return []

    def scalars(self):
        return self

    def __iter__(self):
        return iter(())


async def test_category_histogram_adds_bot_variant_filter() -> None:
    session = _CapturingSession()
    repo = ObjectionRepository(session)  # type: ignore[arg-type]

    await repo.category_histogram(merchant_id=uuid4(), bot_variant="B")
    sql_with = session.statements[-1]
    assert "bot_variant" in sql_with

    await repo.category_histogram(merchant_id=uuid4())
    sql_without = session.statements[-1]
    assert "bot_variant" not in sql_without


async def test_category_histogram_tenant_joins_merchant_and_filters_variant() -> None:
    session = _CapturingSession()
    repo = ObjectionRepository(session)  # type: ignore[arg-type]

    await repo.category_histogram_tenant(tenant_id=uuid4(), bot_variant="A")
    sql = session.statements[-1]
    assert "merchants" in sql  # joined for the tenant aggregation
    assert "bot_variant" in sql


async def test_category_histogram_by_day_tenant_groups_by_day_and_joins_merchant() -> None:
    # #12 — agency objection report needs a per-day, per-category series for the
    # heatmap, scoped tenant-wide (join merchants) and filterable by variant.
    session = _CapturingSession()
    repo = ObjectionRepository(session)  # type: ignore[arg-type]

    await repo.category_histogram_by_day_tenant(tenant_id=uuid4(), bot_variant="A")
    sql = session.statements[-1]
    assert "merchants" in sql  # tenant-wide join
    assert "date_trunc" in sql  # per-day bucketing
    assert "bot_variant" in sql

    await repo.category_histogram_by_day_tenant(tenant_id=uuid4())
    sql_without = session.statements[-1]
    assert "bot_variant" not in sql_without
