"""UC-05 — cumulative signal derivation and update_score action injection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai_core.conversation_service import (
    _responded_within_10min,
    _with_pipeline_advance_action,
    _with_score_action,
)
from ai_core.orchestrator import OrchestratorAction
from ai_core.scoring import derive_conversation_signals, score_lead


def test_behavioural_signals_are_derived_from_state() -> None:
    sig = derive_conversation_signals(
        has_name=True,
        has_email=True,
        turn_count=5,
        sentiment="positive",
        asked_for_booking=True,
        llm_signals={"has_budget": True},
    )
    assert sig["has_name"] and sig["has_email"] and sig["engaged_multiple_turns"]
    assert sig["positive_sentiment"] and sig["asked_for_booking"] and sig["has_budget"]


def test_single_negative_turn_does_not_zero_out_accumulated_score() -> None:
    # Lead with accumulated positives; this turn surfaces a price objection.
    sig = derive_conversation_signals(
        has_name=True,
        has_email=True,
        turn_count=6,
        sentiment="negative",
        asked_for_booking=False,
        llm_signals={"objection_price": True},
    )
    # has_name(5)+has_email(5)+engaged(15)+objection_price(-15) = 10 -> still > 0.
    assert score_lead(sig).score > 0


def test_early_turn_not_yet_engaged() -> None:
    sig = derive_conversation_signals(
        has_name=False,
        has_email=False,
        turn_count=1,
        sentiment="neutral",
        asked_for_booking=False,
        llm_signals={},
    )
    assert sig == {}


def test_with_score_action_appends_when_absent() -> None:
    out = _with_score_action([OrchestratorAction(kind="none", payload={})], {"has_name": True})
    score_actions = [a for a in out if a.kind == "update_score"]
    assert len(score_actions) == 1
    assert score_actions[0].payload["signals"] == {"has_name": True}


def test_with_score_action_merges_into_existing() -> None:
    actions = [OrchestratorAction(kind="update_score", payload={"signals": {"has_budget": True}})]
    out = _with_score_action(actions, {"has_name": True, "has_budget": True})
    score_actions = [a for a in out if a.kind == "update_score"]
    assert len(score_actions) == 1
    assert score_actions[0].payload["signals"] == {"has_name": True, "has_budget": True}


def test_with_score_action_noop_on_empty_signals() -> None:
    actions = [OrchestratorAction(kind="none", payload={})]
    assert _with_score_action(actions, {}) == actions


# --- #4: responded_within_10min derived from prior turn timestamp ------------


def test_responded_within_10min_true_when_recent() -> None:
    prior = datetime.now(UTC) - timedelta(minutes=5)
    assert _responded_within_10min(prior) is True


def test_responded_within_10min_false_when_stale() -> None:
    prior = datetime.now(UTC) - timedelta(minutes=30)
    assert _responded_within_10min(prior) is False


def test_responded_within_10min_false_on_first_turn() -> None:
    assert _responded_within_10min(None) is False


def test_responded_within_10min_handles_naive_datetime() -> None:
    prior = (datetime.now(UTC) - timedelta(minutes=2)).replace(tzinfo=None)
    assert _responded_within_10min(prior) is True


def test_responded_signal_flows_into_derivation() -> None:
    sig = derive_conversation_signals(
        has_name=False,
        has_email=False,
        turn_count=2,
        sentiment="neutral",
        asked_for_booking=False,
        responded_within_10min=True,
        llm_signals={},
    )
    assert sig == {"responded_within_10min": True}


# --- #3: deterministic move_pipeline injection on threshold crossing ---------


def test_pipeline_advance_injected_when_score_crosses() -> None:
    # has_budget(20)+has_timeline(15)+asked_for_booking(20)+has_name(5) = 60 >= 60
    signals = {
        "has_budget": True,
        "has_timeline": True,
        "asked_for_booking": True,
        "has_name": True,
    }
    out = _with_pipeline_advance_action(
        [OrchestratorAction(kind="update_score", payload={})],
        signals,
        advance_threshold=60,
        qualified_stage_id="stage-qualified",
        current_stage_id="stage-new",
    )
    moves = [a for a in out if a.kind == "move_pipeline"]
    assert len(moves) == 1
    assert moves[0].payload["stage_id"] == "stage-qualified"
    assert moves[0].payload["reason"] == "score_threshold_crossed"


def test_pipeline_advance_not_injected_below_threshold() -> None:
    out = _with_pipeline_advance_action(
        [],
        {"has_name": True},  # 5 < 60
        advance_threshold=60,
        qualified_stage_id="stage-qualified",
        current_stage_id="stage-new",
    )
    assert out == []


def test_pipeline_advance_skipped_when_already_qualified() -> None:
    signals = {"has_budget": True, "has_timeline": True, "asked_for_booking": True}
    out = _with_pipeline_advance_action(
        [],
        signals,
        advance_threshold=50,
        qualified_stage_id="stage-qualified",
        current_stage_id="stage-qualified",
    )
    assert out == []


def test_pipeline_advance_skipped_without_qualified_stage_configured() -> None:
    signals = {"has_budget": True, "has_timeline": True, "asked_for_booking": True}
    out = _with_pipeline_advance_action(
        [],
        signals,
        advance_threshold=50,
        qualified_stage_id=None,
        current_stage_id="stage-new",
    )
    assert out == []


def test_pipeline_advance_not_duplicated_when_llm_already_emitted() -> None:
    signals = {"has_budget": True, "has_timeline": True, "asked_for_booking": True}
    existing = [OrchestratorAction(kind="move_pipeline", payload={"stage_id": "x"})]
    out = _with_pipeline_advance_action(
        existing,
        signals,
        advance_threshold=50,
        qualified_stage_id="stage-qualified",
        current_stage_id="stage-new",
    )
    assert out == existing
