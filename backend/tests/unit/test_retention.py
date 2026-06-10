"""GDPR retention sweep: resolves each merchant's window, purges old
conversations, anonymizes stale leads, and emits an audit event per merchant."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest


class FakeSession:
    async def commit(self):
        return None


def _patch(
    monkeypatch,
    *,
    candidates,
    conv_returns=None,
    months=12,
    leads_anon=0,
    cutoff_sink=None,
):
    """Wire the retention module's collaborators with fakes.

    conv_returns: iterable of per-call delete counts (defaults to a single 0).
    """
    from workers.scheduler import retention as mod

    emitted: list[dict] = []
    conv_iter = iter(conv_returns if conv_returns is not None else [0])

    @asynccontextmanager
    async def fake_session_scope():
        yield FakeSession()

    class FakeConvRepo:
        def __init__(self, session): ...
        async def merchants_with_conversations_before(self, cutoff):
            return candidates

        async def delete_older_than(self, *, merchant_id, cutoff, limit=2000):
            if cutoff_sink is not None:
                cutoff_sink.append(cutoff)
            return next(conv_iter, 0)

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def anonymize_stale(self, *, merchant_id, cutoff, limit=2000):
            return leads_anon

    class FakeConfig:
        def __init__(self, session): ...
        async def resolve(self, key, *, merchant_id):
            return months

    class FakeAnalytics:
        def __init__(self, session): ...
        async def emit(self, **kw):
            emitted.append(kw)

    monkeypatch.setattr(mod, "session_scope", fake_session_scope)
    monkeypatch.setattr(mod, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(mod, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(mod, "ConfigResolver", FakeConfig)
    monkeypatch.setattr(mod, "AnalyticsRepository", FakeAnalytics)
    return emitted


async def test_purges_conversations_per_merchant(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.scheduler import retention as mod

    m1, t1 = uuid4(), uuid4()
    emitted = _patch(monkeypatch, candidates=[(m1, t1)], conv_returns=[5], months=12)

    result = await mod.enforce_retention({})

    assert result == {
        "merchants": 1,
        "conversations_deleted": 5,
        "leads_anonymized": 0,
        "budget_hit": False,
    }
    assert emitted[0]["event_type"] == "retention.purged"
    assert emitted[0]["properties"]["retention_months"] == 12
    assert emitted[0]["properties"]["conversations_deleted"] == 5


async def test_no_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.scheduler import retention as mod

    emitted = _patch(monkeypatch, candidates=[])
    result = await mod.enforce_retention({})
    assert result == {
        "merchants": 0,
        "conversations_deleted": 0,
        "leads_anonymized": 0,
        "budget_hit": False,
    }
    assert emitted == []


async def test_anonymizes_stale_leads(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.scheduler import retention as mod

    m1, t1 = uuid4(), uuid4()
    # No conversations to purge, but 3 stale leads to anonymize.
    emitted = _patch(monkeypatch, candidates=[(m1, t1)], conv_returns=[0], leads_anon=3)

    result = await mod.enforce_retention({})

    assert result["leads_anonymized"] == 3
    assert result["conversations_deleted"] == 0
    assert emitted[0]["properties"]["leads_anonymized"] == 3


async def test_zero_retention_clamps_to_floor_not_now(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.scheduler import retention as mod

    cutoffs: list = []
    _patch(
        monkeypatch,
        candidates=[(uuid4(), uuid4())],
        conv_returns=[0],
        months=0,
        cutoff_sink=cutoffs,
    )

    await mod.enforce_retention({})

    # A stray 0 must NOT collapse the cutoff to ~now; it's clamped to the floor.
    assert cutoffs
    age_days = (datetime.now(tz=UTC) - cutoffs[0]).days
    assert age_days >= mod.MIN_RETENTION_MONTHS * 30 - 1


async def test_drains_multiple_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.scheduler import retention as mod

    m1, t1 = uuid4(), uuid4()
    emitted = _patch(
        monkeypatch,
        candidates=[(m1, t1)],
        conv_returns=[mod._BATCH, mod._BATCH, 7],  # two full batches then a partial
        months=24,
    )

    result = await mod.enforce_retention({})
    assert result["conversations_deleted"] == mod._BATCH * 2 + 7
    assert emitted[0]["properties"]["conversations_deleted"] == mod._BATCH * 2 + 7
