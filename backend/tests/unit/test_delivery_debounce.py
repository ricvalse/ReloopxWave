"""Debounce: pure decision + the worker buffer/supersede/flush flow.

The worker is exercised against a dict-backed FakeRedis (with a chained
pipeline), a recording FakeArq pool, and a fake ConversationService — no DB/IO.
"""

from __future__ import annotations

import types
import uuid
from typing import Any

import pytest
from workers.conversation import handlers as wh

from ai_core.conversation_service import InboundResult, PersistOutcome
from ai_core.delivery import Flush, RescheduleBy, debounce_decision


def test_debounce_decision_pure() -> None:
    assert isinstance(debounce_decision(10.0, 5.0), Flush)
    d = debounce_decision(5.0, 10.0)
    assert isinstance(d, RescheduleBy)
    assert d.seconds == 5.0


# ---- fakes ----------------------------------------------------------------


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple] = []

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def rpush(self, key: str, val: Any) -> FakePipeline:
        self._ops.append(("rpush", key, val))
        return self

    def expire(self, key: str, ttl: int) -> FakePipeline:
        self._ops.append(("expire", key, ttl))
        return self

    def set(self, key: str, val: Any, ex: int | None = None) -> FakePipeline:
        self._ops.append(("set", key, val))
        return self

    def lrange(self, key: str, start: int, end: int) -> FakePipeline:
        self._ops.append(("lrange", key))
        return self

    def delete(self, key: str) -> FakePipeline:
        self._ops.append(("delete", key))
        return self

    async def execute(self) -> list[Any]:
        return [self._redis._apply(op) for op in self._ops]


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list] = {}
        self.strings: dict[str, Any] = {}

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)

    async def get(self, key: str) -> Any:
        return self.strings.get(key)

    def _apply(self, op: tuple) -> Any:
        kind = op[0]
        if kind == "rpush":
            self.lists.setdefault(op[1], []).append(op[2])
            return len(self.lists[op[1]])
        if kind == "expire":
            return True
        if kind == "set":
            self.strings[op[1]] = op[2]
            return True
        if kind == "lrange":
            return list(self.lists.get(op[1], []))
        if kind == "delete":
            self.lists.pop(op[1], None)
            self.strings.pop(op[1], None)
            return 1
        raise AssertionError(f"unknown op {kind}")


class FakeArq:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    async def enqueue_job(self, name: str, *args: Any, _job_id=None, _defer_by=None) -> None:
        self.jobs.append({"name": name, "args": args, "job_id": _job_id, "defer_by": _defer_by})


class FakeService:
    def __init__(self, outcome: PersistOutcome) -> None:
        self.outcome = outcome
        self.persist_calls: list[dict] = []
        self.reply_calls: list[dict] = []

    async def handle_inbound_persist(self, **kw: Any) -> PersistOutcome:
        self.persist_calls.append(kw)
        return self.outcome

    async def generate_and_send_reply(self, **kw: Any) -> InboundResult:
        self.reply_calls.append(kw)
        return InboundResult(handled=True, conversation_id=self.outcome.conversation_id)


def _ctx(service: FakeService, redis: FakeRedis, arq: FakeArq) -> dict:
    return {
        "runtime": types.SimpleNamespace(conversation_service=service),
        "config_redis": redis,
        "redis": arq,
    }


def _outcome(window: int) -> tuple[PersistOutcome, uuid.UUID, uuid.UUID]:
    mid, cid = uuid.uuid4(), uuid.uuid4()
    return (
        PersistOutcome(
            handled=True,
            auto_reply_on=True,
            conversation_id=cid,
            merchant_id=mid,
            debounce_window_s=window,
        ),
        mid,
        cid,
    )


# ---- tests ----------------------------------------------------------------


async def test_debounce_buffers_then_flushes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock(1000.0)
    monkeypatch.setattr(wh, "time", clock)
    outcome, mid, _cid = _outcome(window=5)
    service = FakeService(outcome)
    redis, arq = FakeRedis(), FakeArq()
    ctx = _ctx(service, redis, arq)

    r1 = await wh.handle_inbound_message(ctx, "PNID", "39333", "ciao", "wamid.1")
    assert r1["reason"] == "debounced"

    clock.now = 1002.0  # second message, 2s later
    r2 = await wh.handle_inbound_message(ctx, "PNID", "39333", "ci sei?", "wamid.2")
    assert r2["reason"] == "debounced"

    assert service.reply_calls == []  # nothing sent yet
    buf_key, due_key, job_id = wh._debounce_keys(str(mid), "39333")
    assert len(redis.lists[buf_key]) == 2
    assert [j["job_id"] for j in arq.jobs] == [job_id, job_id]  # stable supersede id
    assert redis.strings[due_key] == 1007.0  # pushed out by message 2 (1002 + 5)

    clock.now = 1008.0  # quiet period elapsed
    res = await wh.flush_inbound_reply(ctx, str(mid), "39333", "PNID")
    assert res["flushed"] is True
    assert res["messages"] == 2
    assert len(service.reply_calls) == 1
    call = service.reply_calls[0]
    assert call["text"] == "ciao\nci sei?"
    assert call["exclude_wa_message_ids"] == ["wamid.1", "wamid.2"]
    assert call["wa_message_id"] == "wamid.2"
    assert buf_key not in redis.lists  # drained

    # A re-run finds an empty buffer and does not reply again.
    res2 = await wh.flush_inbound_reply(ctx, str(mid), "39333", "PNID")
    assert res2["flushed"] is False
    assert len(service.reply_calls) == 1


async def test_no_debounce_replies_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock(2000.0)
    monkeypatch.setattr(wh, "time", clock)
    outcome, _mid, _cid = _outcome(window=0)
    service = FakeService(outcome)
    redis, arq = FakeRedis(), FakeArq()
    ctx = _ctx(service, redis, arq)

    await wh.handle_inbound_message(ctx, "PNID", "39333", "ciao", "wamid.1")

    assert len(service.reply_calls) == 1
    assert service.reply_calls[0]["exclude_wa_message_ids"] == ["wamid.1"]
    assert redis.lists == {}  # nothing buffered
    assert arq.jobs == []  # no flush scheduled


async def test_flush_reschedules_when_not_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock(1000.0)
    monkeypatch.setattr(wh, "time", clock)
    outcome, mid, _cid = _outcome(window=5)
    service = FakeService(outcome)
    redis, arq = FakeRedis(), FakeArq()
    ctx = _ctx(service, redis, arq)

    await wh.handle_inbound_message(ctx, "PNID", "39333", "ciao", "wamid.1")  # due = 1005

    clock.now = 1002.0  # still before due → must reschedule, not reply
    res = await wh.flush_inbound_reply(ctx, str(mid), "39333", "PNID")
    assert res["reason"] == "rescheduled"
    assert service.reply_calls == []
    assert any(j["name"] == "flush_inbound_reply" for j in arq.jobs)
