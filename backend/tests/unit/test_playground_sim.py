"""UC-08 — pure dry-run simulation of the bot's actions."""

from __future__ import annotations

from ai_core.orchestrator import OrchestratorAction
from ai_core.playground_sim import PlaygroundLeadState, simulate_turn


def _sim(actions, state=None, sentiment=None, history_len=0):
    return simulate_turn(
        actions=actions,
        state=state or PlaygroundLeadState(),
        current_sentiment=sentiment,
        hot_threshold=80,
        cold_threshold=30,
        qualified_stage_default="stage-qualified",
        history_len=history_len,
    )


def test_scoring_event_always_present_and_turn_increments() -> None:
    res = _sim([])
    assert res.state.turn_count == 1
    score_events = [e for e in res.events if e.kind == "update_score"]
    assert len(score_events) == 1
    assert score_events[0].detail["previous_score"] == 0


def test_book_slot_simulated_success_adds_confirmation_and_captures_contact() -> None:
    action = OrchestratorAction(
        kind="book_slot",
        payload={
            "preferred_start_iso": "2026-07-01T15:00:00",
            "contact_fields": {"name": "Marco", "email": "marco@example.com"},
        },
    )
    res = _sim([action])

    assert res.state.booked is True
    assert res.state.lead_name == "Marco"
    assert res.state.lead_email == "marco@example.com"
    # The bot would send a separate confirmation message (extra bubble).
    assert res.extra_bubbles and "ho prenotato" in res.extra_bubbles[0]
    assert any(e.kind == "book_slot" for e in res.events)
    # asked_for_booking (+20) + has_name (+5) + has_email (+5) drive the score.
    score = next(e for e in res.events if e.kind == "update_score")
    assert score.detail["new_score"] == 30
    assert "asked_for_booking" in score.detail["reason_codes"]


def test_move_pipeline_uses_payload_then_default() -> None:
    explicit = _sim([OrchestratorAction(kind="move_pipeline", payload={"stage_id": "won"})])
    assert explicit.state.pipeline_stage == "won"

    fallback = _sim([OrchestratorAction(kind="move_pipeline", payload={})])
    assert fallback.state.pipeline_stage == "stage-qualified"
    assert any(e.kind == "move_pipeline" for e in fallback.events)


def test_escalate_human_sets_flag_and_reason() -> None:
    res = _sim(
        [OrchestratorAction(kind="escalate_human", payload={"reason": "cliente arrabbiato"})]
    )
    assert res.state.escalated is True
    event = next(e for e in res.events if e.kind == "escalate_human")
    assert "cliente arrabbiato" in event.summary


def test_positive_sentiment_and_engagement_accumulate() -> None:
    # Parity with prod: scoring turn_count = history_len + 1. With 2 prior
    # messages (user + assistant) → 3 → engaged_multiple_turns (+15);
    # positive sentiment (+10).
    res = _sim([], sentiment="positive", history_len=2)
    score = next(e for e in res.events if e.kind == "update_score")
    assert "engaged_multiple_turns" in score.detail["reason_codes"]
    assert "positive_sentiment" in score.detail["reason_codes"]
    assert score.detail["new_score"] == 25  # 15 + 10


def test_engagement_threshold_matches_production_turn_count() -> None:
    # history_len 1 → scoring turn_count 2 → NOT engaged yet (prod parity).
    not_yet = _sim([], history_len=1)
    assert (
        "engaged_multiple_turns"
        not in next(e for e in not_yet.events if e.kind == "update_score").detail["reason_codes"]
    )
    # history_len 2 → scoring turn_count 3 → engaged.
    engaged = _sim([], history_len=2)
    assert (
        "engaged_multiple_turns"
        in next(e for e in engaged.events if e.kind == "update_score").detail["reason_codes"]
    )


def test_llm_content_signals_are_whitelisted() -> None:
    action = OrchestratorAction(
        kind="update_score",
        payload={"signals": {"has_budget": True, "bogus_key": True}},
    )
    res = _sim([action])
    score = next(e for e in res.events if e.kind == "update_score")
    assert "has_budget" in score.detail["reason_codes"]
    assert "bogus_key" not in score.detail["reason_codes"]


def test_state_round_trips_through_dict() -> None:
    state = PlaygroundLeadState(lead_score=42, lead_name="Lia", booked=True, turn_count=5)
    restored = PlaygroundLeadState.from_dict(state.to_dict())
    assert restored == state
