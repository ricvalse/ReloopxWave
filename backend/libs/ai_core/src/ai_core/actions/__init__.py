"""Registered handlers for OrchestratorAction kinds.

Each handler implements the ActionHandler protocol (async callable taking the
action and a TurnContext). They are registered on the ActionDispatcher at
worker startup.

Adding a new action kind: define the handler here, register it in
`workers/runtime.py`. The orchestrator produces the action via structured
output; the dispatcher routes it.
"""
from ai_core.actions.booking import BookSlotHandler
from ai_core.actions.pipeline import MovePipelineHandler
from ai_core.actions.scoring import UpdateScoreHandler, derive_signals_from_llm_payload

__all__ = ["BookSlotHandler", "MovePipelineHandler", "UpdateScoreHandler", "derive_signals_from_llm_payload"]
