"""Explicit conversation state machine.

Each conversation moves through a linear funnel with lateral moves for
objections and terminal states for BOOKED / DEAD / ESCALATED. The current
state is persisted in ``conversations.current_state`` and injected into the
system prompt so the model knows where it stands.

Transitions are driven by the orchestrator actions returned after each turn.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_core.orchestrator import OrchestratorAction


class ConvState(str, Enum):
    GREETING = "GREETING"
    QUALIFYING = "QUALIFYING"
    PITCHING = "PITCHING"
    OBJECTION_HANDLING = "OBJECTION_HANDLING"
    CLOSING = "CLOSING"
    BOOKED = "BOOKED"
    DEAD = "DEAD"
    ESCALATED = "ESCALATED"


# Human-readable descriptions injected into the system prompt.
_STATE_HINTS: dict[ConvState, str] = {
    ConvState.GREETING: (
        "Stai accogliendo il lead per la prima volta. Presentati, metti a proprio agio "
        "e inizia a capire il suo bisogno principale."
    ),
    ConvState.QUALIFYING: (
        "Stai raccogliendo le informazioni chiave (nome, esigenza, budget, tempistiche). "
        "Fai una domanda alla volta."
    ),
    ConvState.PITCHING: (
        "Hai le informazioni necessarie. Presenta l'offerta in modo convincente, "
        "personalizzandola sul bisogno espresso."
    ),
    ConvState.OBJECTION_HANDLING: (
        "Il lead ha sollevato un'obiezione. Ascolta, valida il dubbio e rispondi "
        "con dati o rassicurazioni concrete."
    ),
    ConvState.CLOSING: (
        "Il lead è vicino alla conversione. Proponi la prenotazione o il passo "
        "successivo concreto. Non divagare."
    ),
    ConvState.BOOKED: (
        "Il lead ha prenotato. Conferma, ringrazierà e chiudi cordialmente."
    ),
    ConvState.ESCALATED: (
        "La conversazione è in carico a un operatore umano. "
        "Non rispondere automaticamente."
    ),
    ConvState.DEAD: (
        "Il lead ha smesso di rispondere o si è disinteressato. "
        "Non inviare messaggi automatici."
    ),
}


def state_system_hint(state: ConvState) -> str:
    """Short Italian instruction for the current FSM state, injected into the system prompt."""
    hint = _STATE_HINTS.get(state, "")
    return f"[Fase conversazione: {state.value}] {hint}" if hint else ""


def next_state(
    current: ConvState,
    actions: list[OrchestratorAction],
    turn_count: int = 0,
) -> ConvState:
    """Derive the next FSM state from the orchestrator actions returned by the turn.

    Terminal states (BOOKED, ESCALATED, DEAD) are sticky — they never transition
    back. For all others, the highest-priority matching action wins.
    """
    if current in (ConvState.BOOKED, ConvState.ESCALATED, ConvState.DEAD):
        return current

    kinds = {a.kind for a in actions}
    signals: dict[str, bool] = {}
    for a in actions:
        if a.kind == "update_score":
            signals.update(a.payload.get("signals", {}))

    # Terminal transitions — highest priority
    if "escalate_human" in kinds:
        return ConvState.ESCALATED
    if "book_slot" in kinds:
        # stays in CLOSING until booking is confirmed; the worker flips to BOOKED
        # once the GHL round-trip succeeds. Here we just advance to CLOSING.
        return ConvState.CLOSING

    # Objection signals
    has_objection = any(
        signals.get(k) for k in ("objection_price", "objection_trust", "objection_competitor")
    )
    if has_objection and current not in (ConvState.CLOSING,):
        return ConvState.OBJECTION_HANDLING

    # Recovery from objection handling
    if current == ConvState.OBJECTION_HANDLING and not has_objection:
        return ConvState.PITCHING

    # Booking intent / closing
    if signals.get("asked_for_booking") or "propose_slots" in kinds:
        return ConvState.CLOSING

    # Pipeline advance = pitching phase cleared
    if "move_pipeline" in kinds and current in (ConvState.QUALIFYING, ConvState.PITCHING):
        return ConvState.CLOSING

    # Qualifying → pitching when we have substantive lead data
    has_budget_or_timeline = signals.get("has_budget") or signals.get("has_timeline")
    has_name_or_email = signals.get("has_name") or signals.get("has_email")
    if current == ConvState.QUALIFYING and (has_budget_or_timeline or has_name_or_email):
        return ConvState.PITCHING

    # Greeting → qualifying after the very first AI turn
    if current == ConvState.GREETING and turn_count >= 1:
        return ConvState.QUALIFYING

    return current
