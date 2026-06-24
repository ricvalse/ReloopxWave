"""UC-05 — dropped_off is derived from real abandonment.

When `close_idle_conversations` closes a silent conversation, the lead is marked
`dropped_off` and rescored. These tests stub the DB layer and verify the rescore
path: the signal is OR-merged into accumulated content signals, the score is
recomputed with `score_lead` over the same {accumulated content + behavioural}
set the live handler uses (behavioural recovered from `score_reasons`, since
those signals aren't persisted), and a `lead_score_changed` event is emitted.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, ClassVar

import pytest
import workers.scheduler.close_conversations as mod


class _FakeLead:
    def __init__(self, score: int, score_reasons: list[str] | None = None) -> None:
        self.score = score
        self.score_reasons = score_reasons or []


class _FakeLeadRepo:
    instances: ClassVar[list[_FakeLeadRepo]] = []

    def __init__(self, session: Any) -> None:
        # A lead that last scored hot off behavioural + content signals:
        # has_name(5)+engaged_multiple_turns(15)+asked_for_booking(20)+
        # positive_sentiment(10)+has_budget(20) = 70.
        self._lead = _FakeLead(
            score=70,
            score_reasons=[
                "has_name",
                "engaged_multiple_turns",
                "asked_for_booking",
                "positive_sentiment",
                "has_budget",
            ],
        )
        self.merged: dict[str, bool] | None = None
        self.updated: dict[str, Any] | None = None
        _FakeLeadRepo.instances.append(self)

    async def get(self, lead_id: uuid.UUID) -> _FakeLead:
        return self._lead

    async def merge_content_signals(
        self, lead_id: uuid.UUID, new_signals: dict[str, bool]
    ) -> dict[str, bool]:
        # Pretend the lead already had a confirmed budget; OR in the new signal.
        self.merged = {"has_budget": True, **{k: v for k, v in new_signals.items() if v}}
        return self.merged

    async def update_score(self, lead_id: uuid.UUID, *, score: int, reasons: list[str]) -> None:
        self.updated = {"score": score, "reasons": reasons}


class _FakeAnalyticsRepo:
    events: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, session: Any) -> None: ...

    async def emit(self, **kwargs: Any) -> None:
        _FakeAnalyticsRepo.events.append(kwargs)


@asynccontextmanager
async def _fake_tenant_session(ctx: Any):
    yield object()


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    _FakeLeadRepo.instances.clear()
    _FakeAnalyticsRepo.events.clear()


async def test_rescore_marks_dropped_off_and_recomputes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "tenant_session", _fake_tenant_session)
    monkeypatch.setattr(mod, "LeadRepository", _FakeLeadRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", _FakeAnalyticsRepo)

    lead_id, merchant_id, tenant_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rescored = await mod._rescore_dropped_off([(lead_id, merchant_id, tenant_id)])

    assert rescored == 1
    repo = _FakeLeadRepo.instances[0]
    # dropped_off OR-merged into accumulated content signals.
    assert repo.merged == {"has_budget": True, "dropped_off": True}
    # The rescore must score over {accumulated content + behavioural}, exactly
    # like the live update_score handler — NOT content-only. Behavioural signals
    # recovered from score_reasons must be preserved, so the lead only loses the
    # intended -20 penalty rather than cratering:
    #   has_name(5)+engaged_multiple_turns(15)+asked_for_booking(20)+
    #   positive_sentiment(10)+has_budget(20)+dropped_off(-20) = 50
    assert repo.updated is not None
    assert repo.updated["score"] == 50
    reasons = set(repo.updated["reasons"])
    # Behavioural contributions survive the rescore (the bug dropped these).
    assert {"has_name", "engaged_multiple_turns", "asked_for_booking", "positive_sentiment"} <= (
        reasons
    )
    assert "has_budget" in reasons  # accumulated content preserved
    assert "dropped_off" in reasons  # the abandonment penalty applied
    assert len(_FakeAnalyticsRepo.events) == 1
    evt = _FakeAnalyticsRepo.events[0]
    assert evt["event_type"] == "lead_score_changed"
    assert evt["properties"]["trigger"] == "dropped_off"
    assert evt["properties"]["previous_score"] == 70
    # New score is the prior 70 minus the single -20 drop-off penalty.
    assert evt["properties"]["new_score"] == 50


async def test_rescore_preserves_behavioural_only_lead(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a lead whose score is purely behavioural (no content signals)
    must not crater to 0 on drop-off. The old content-only rescore dropped every
    behavioural contribution; the fix recovers them from score_reasons so only
    the -20 penalty applies (floored at 0)."""

    class _BehaviouralRepo(_FakeLeadRepo):
        def __init__(self, session: Any) -> None:
            super().__init__(session)
            # has_name(5)+engaged_multiple_turns(15)+asked_for_booking(20) = 40,
            # no content signals on file.
            self._lead = _FakeLead(
                score=40,
                score_reasons=["has_name", "engaged_multiple_turns", "asked_for_booking"],
            )

        async def merge_content_signals(
            self, lead_id: uuid.UUID, new_signals: dict[str, bool]
        ) -> dict[str, bool]:
            # Only the just-added dropped_off accumulates (no prior content).
            self.merged = {k: v for k, v in new_signals.items() if v}
            return self.merged

    monkeypatch.setattr(mod, "tenant_session", _fake_tenant_session)
    monkeypatch.setattr(mod, "LeadRepository", _BehaviouralRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", _FakeAnalyticsRepo)

    rescored = await mod._rescore_dropped_off([(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())])
    assert rescored == 1
    repo = _FakeLeadRepo.instances[0]
    assert repo.updated is not None
    # 40 (behavioural) - 20 (dropped_off) = 20, NOT 0.
    assert repo.updated["score"] == 20
    reasons = set(repo.updated["reasons"])
    assert {"has_name", "engaged_multiple_turns", "asked_for_booking", "dropped_off"} == reasons


async def test_rescore_isolates_per_lead_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "tenant_session", _fake_tenant_session)
    monkeypatch.setattr(mod, "AnalyticsRepository", _FakeAnalyticsRepo)

    class _BoomRepo(_FakeLeadRepo):
        async def get(self, lead_id: uuid.UUID) -> _FakeLead:
            raise RuntimeError("db down")

    monkeypatch.setattr(mod, "LeadRepository", _BoomRepo)
    rescored = await mod._rescore_dropped_off([(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())])
    # The failure is swallowed; the sweep keeps going.
    assert rescored == 0


async def test_rescore_empty_targets_is_noop() -> None:
    assert await mod._rescore_dropped_off([]) == 0
