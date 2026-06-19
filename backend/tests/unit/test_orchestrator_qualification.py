"""The merchant's `pipeline.advance_threshold` (+ live lead score) must reach
the model as move_pipeline decision context — previously the threshold was an
orphaned config key with zero consumers.
"""

from __future__ import annotations

import uuid

from ai_core.orchestrator import ConversationContext, ConversationOrchestrator


def _ctx(*, lead_score: int, advance_threshold: int) -> ConversationContext:
    return ConversationContext(
        merchant_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        lead_score=lead_score,
        hot_threshold=80,
        system_prompt="SYSTEM",
        advance_threshold=advance_threshold,
    )


def test_advance_threshold_and_score_injected_into_system_message() -> None:
    orch = ConversationOrchestrator(router=object())  # type: ignore[arg-type]
    messages = orch._build_messages(_ctx(lead_score=55, advance_threshold=70), "ciao")

    system = next(m.content for m in messages if m.role == "system")
    assert "punteggio attuale 55/100" in system
    assert "soglia di avanzamento pipeline configurata dal merchant 70" in system
    assert "move_pipeline" in system


def test_advance_threshold_default_when_unset() -> None:
    orch = ConversationOrchestrator(router=object())  # type: ignore[arg-type]
    # ConversationContext defaults advance_threshold to 60 when not provided.
    ctx = ConversationContext(
        merchant_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        lead_id=None,
        lead_score=0,
        hot_threshold=80,
        system_prompt="SYSTEM",
    )
    system = next(m.content for m in orch._build_messages(ctx, "ciao") if m.role == "system")
    assert "configurata dal merchant 60" in system
