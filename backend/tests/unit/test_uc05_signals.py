"""UC-05 — cumulative signal derivation and update_score action injection."""
from __future__ import annotations

from ai_core.conversation_service import _with_score_action
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
