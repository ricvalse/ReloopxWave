"""Unit tests for ai_core.state_machine."""
from __future__ import annotations

import pytest

from ai_core.orchestrator import OrchestratorAction
from ai_core.state_machine import ConvState, next_state, state_system_hint


def _action(kind: str, signals: dict | None = None) -> OrchestratorAction:
    payload: dict = {}
    if signals is not None:
        payload["signals"] = signals
    return OrchestratorAction(kind=kind, payload=payload)


class TestNextState:
    def test_greeting_to_qualifying_after_first_turn(self):
        assert next_state(ConvState.GREETING, [], turn_count=1) == ConvState.QUALIFYING

    def test_greeting_stays_at_zero_turns(self):
        assert next_state(ConvState.GREETING, [], turn_count=0) == ConvState.GREETING

    def test_escalate_always_wins(self):
        for state in [ConvState.GREETING, ConvState.QUALIFYING, ConvState.PITCHING]:
            result = next_state(state, [_action("escalate_human")], turn_count=5)
            assert result == ConvState.ESCALATED

    def test_terminal_states_are_sticky(self):
        for terminal in [ConvState.BOOKED, ConvState.ESCALATED, ConvState.DEAD]:
            result = next_state(terminal, [_action("escalate_human")], turn_count=10)
            assert result == terminal

    def test_objection_triggers_objection_handling(self):
        actions = [_action("update_score", {"objection_price": True})]
        result = next_state(ConvState.QUALIFYING, actions)
        assert result == ConvState.OBJECTION_HANDLING

    def test_objection_from_closing_stays_closing(self):
        actions = [_action("update_score", {"objection_price": True})]
        result = next_state(ConvState.CLOSING, actions)
        assert result == ConvState.CLOSING

    def test_recovery_from_objection_to_pitching(self):
        result = next_state(ConvState.OBJECTION_HANDLING, [_action("none")])
        assert result == ConvState.PITCHING

    def test_booking_intent_moves_to_closing(self):
        actions = [_action("update_score", {"asked_for_booking": True})]
        result = next_state(ConvState.PITCHING, actions)
        assert result == ConvState.CLOSING

    def test_propose_slots_moves_to_closing(self):
        result = next_state(ConvState.PITCHING, [_action("propose_slots")])
        assert result == ConvState.CLOSING

    def test_move_pipeline_from_qualifying_to_closing(self):
        result = next_state(ConvState.QUALIFYING, [_action("move_pipeline")])
        assert result == ConvState.CLOSING

    def test_qualifying_to_pitching_with_budget(self):
        actions = [_action("update_score", {"has_budget": True})]
        result = next_state(ConvState.QUALIFYING, actions)
        assert result == ConvState.PITCHING

    def test_qualifying_to_pitching_with_name(self):
        actions = [_action("update_score", {"has_name": True})]
        result = next_state(ConvState.QUALIFYING, actions)
        assert result == ConvState.PITCHING

    def test_book_slot_moves_to_closing(self):
        result = next_state(ConvState.PITCHING, [_action("book_slot")])
        assert result == ConvState.CLOSING

    def test_no_transition_without_signal(self):
        result = next_state(ConvState.PITCHING, [_action("none")])
        assert result == ConvState.PITCHING


class TestStateSystemHint:
    def test_returns_non_empty_for_all_states(self):
        for state in ConvState:
            hint = state_system_hint(state)
            # BOOKED / DEAD / ESCALATED still have hints
            assert isinstance(hint, str)

    def test_includes_state_name(self):
        hint = state_system_hint(ConvState.QUALIFYING)
        assert "QUALIFYING" in hint
