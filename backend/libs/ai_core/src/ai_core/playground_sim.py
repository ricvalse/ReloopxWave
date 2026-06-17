"""UC-08 — pure dry-run simulation of the bot's actions (tools).

The live action handlers (book_slot / move_pipeline / update_score /
escalate_human) write to the DB, call GoHighLevel, and send WhatsApp. The
playground must do NONE of that, so this module re-implements the *observable
outcome* of each action as a pure, side-effect-free transformation: given the
LLM's actions + the carried simulated lead state + this turn's sentiment, it
returns human-readable events, the evolved lead state, and any extra assistant
bubbles (e.g. the booking confirmation the bot would send).

It re-uses the production scoring / classify / booking-confirmation logic so the
dry-run matches what would really happen. No IO, no clock, no persistence.

Fidelity notes:
- The lead score is the always-on cumulative score (`derive_conversation_signals`
  + `score_lead`), recomputed every turn — this is the lead's score of record in
  production (UC-05). We deliberately do NOT replicate the booking handler's
  transient `score=100`: the cumulative `update_score` is the canonical score.
- A booking is assumed to succeed (the playground has no GHL to check slot
  availability), so it always yields the "Perfetto, ho prenotato…" confirmation.
- name/email are captured from the action `contact_fields` so `has_name`/
  `has_email` evolve like a real (GHL-synced) lead — in production these columns
  are only populated by a GHL sync, not by the turn itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from ai_core.actions.booking import _format_human, format_booking_confirmation
from ai_core.actions.scoring import classify_temperature, derive_signals_from_llm_payload
from ai_core.orchestrator import OrchestratorAction
from ai_core.scoring import derive_conversation_signals, score_lead


@dataclass(slots=True)
class PlaygroundLeadState:
    """The simulated lead state carried turn-to-turn by the client."""

    lead_score: int = 0
    lead_sentiment: str | None = None
    lead_name: str | None = None
    lead_email: str | None = None
    pipeline_stage: str | None = None
    booked: bool = False
    escalated: bool = False
    turn_count: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PlaygroundLeadState:
        data = data or {}
        return cls(
            lead_score=int(data.get("lead_score") or 0),
            lead_sentiment=data.get("lead_sentiment"),
            lead_name=data.get("lead_name"),
            lead_email=data.get("lead_email"),
            pipeline_stage=data.get("pipeline_stage"),
            booked=bool(data.get("booked", False)),
            escalated=bool(data.get("escalated", False)),
            turn_count=int(data.get("turn_count") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "lead_score": self.lead_score,
            "lead_sentiment": self.lead_sentiment,
            "lead_name": self.lead_name,
            "lead_email": self.lead_email,
            "pipeline_stage": self.pipeline_stage,
            "booked": self.booked,
            "escalated": self.escalated,
            "turn_count": self.turn_count,
        }


@dataclass(slots=True)
class SimulatedActionEvent:
    """A human-readable "this is what the bot would do" entry for the UI."""

    kind: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "summary": self.summary, "detail": self.detail}


@dataclass(slots=True)
class SimulationResult:
    events: list[SimulatedActionEvent]
    state: PlaygroundLeadState
    extra_bubbles: list[str]


def _contact_from_payload(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    cf = payload.get("contact_fields")
    if not isinstance(cf, dict):
        return None, None
    # Same preference order as the live booking handler (first_name → name).
    raw_name = cf.get("first_name") or cf.get("name")
    raw_email = cf.get("email")
    name = str(raw_name).strip() if raw_name else None
    email = str(raw_email).strip() if raw_email else None
    return name or None, email or None


def simulate_turn(
    *,
    actions: list[OrchestratorAction],
    state: PlaygroundLeadState,
    current_sentiment: str | None,
    hot_threshold: int,
    cold_threshold: int,
    qualified_stage_default: str | None,
    history_len: int = 0,
) -> SimulationResult:
    """Replay the LLM's actions against the carried state, side-effect free.

    `history_len` is the number of prior chat messages (user + assistant), so the
    scoring `turn_count` matches production exactly (`len(chat_history) + 1`),
    while `state.turn_count` stays a human-friendly per-turn counter for display.
    """
    new = replace(state)
    new.turn_count = state.turn_count + 1
    events: list[SimulatedActionEvent] = []
    extra_bubbles: list[str] = []

    # Capture contact identity from any action carrying contact_fields first, so
    # has_name/has_email reflect this turn when the score is computed below.
    for action in actions:
        if action.kind in ("book_slot", "move_pipeline"):
            name, email = _contact_from_payload(action.payload)
            if name and not new.lead_name:
                new.lead_name = name
            if email and not new.lead_email:
                new.lead_email = email

    asked_for_booking = any(a.kind == "book_slot" for a in actions)

    # book_slot — assume success (no GHL to verify availability).
    booking_action = next((a for a in actions if a.kind == "book_slot"), None)
    if booking_action is not None:
        raw_slot = booking_action.payload.get("preferred_start_iso")
        slot_iso = str(raw_slot) if raw_slot else None
        extra_bubbles.append(
            format_booking_confirmation(booked=True, slot_start_iso=slot_iso, suggested=[])
        )
        new.booked = True
        who = new.lead_name or "il contatto"
        when = f" — {_format_human(slot_iso)}" if slot_iso else ""
        events.append(
            SimulatedActionEvent(
                kind="book_slot",
                summary=f"Prenotazione simulata per {who}{when}",
                detail={
                    "slot_start_iso": slot_iso,
                    "lead_name": new.lead_name,
                    "lead_email": new.lead_email,
                },
            )
        )

    # move_pipeline
    move_action = next((a for a in actions if a.kind == "move_pipeline"), None)
    if move_action is not None:
        raw_stage = move_action.payload.get("stage_id") or qualified_stage_default
        stage = str(raw_stage) if raw_stage else None
        new.pipeline_stage = stage
        events.append(
            SimulatedActionEvent(
                kind="move_pipeline",
                summary=f"Spostato in pipeline → {stage or 'stage qualificato'}",
                detail={"stage": stage},
            )
        )

    # escalate_human
    escalate_action = next((a for a in actions if a.kind == "escalate_human"), None)
    if escalate_action is not None:
        raw_reason = escalate_action.payload.get("reason")
        reason = str(raw_reason).strip() if raw_reason else None
        new.escalated = True
        tail = f" — {reason}" if reason else ""
        events.append(
            SimulatedActionEvent(
                kind="escalate_human",
                summary=f"Escalation a operatore umano{tail}",
                detail={"reason": reason},
            )
        )

    # Always-on cumulative scoring (UC-05): recompute every turn, exactly like
    # production, merging behavioural signals with any LLM content signals.
    update_action = next((a for a in actions if a.kind == "update_score"), None)
    llm_signals = derive_signals_from_llm_payload(update_action.payload) if update_action else {}
    merged = derive_conversation_signals(
        has_name=bool(new.lead_name),
        has_email=bool(new.lead_email),
        turn_count=history_len + 1,
        sentiment=current_sentiment,
        asked_for_booking=asked_for_booking,
        llm_signals=llm_signals,
    )
    scored = score_lead(merged)
    previous_score = state.lead_score
    new.lead_score = scored.score
    temperature = classify_temperature(scored.score, hot_threshold, cold_threshold)
    previous_temp = classify_temperature(previous_score, hot_threshold, cold_threshold)
    events.append(
        SimulatedActionEvent(
            kind="update_score",
            summary=f"Score lead: {previous_score} → {scored.score} ({temperature})",
            detail={
                "previous_score": previous_score,
                "new_score": scored.score,
                "temperature": temperature,
                "previous_temperature": previous_temp,
                "reason_codes": scored.reason_codes,
            },
        )
    )

    return SimulationResult(events=events, state=new, extra_bubbles=extra_bubbles)
