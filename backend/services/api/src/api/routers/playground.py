from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ai_core import (
    PlaygroundLeadState,
    PlaygroundMessage,
    PlaygroundRequest,
    PlaygroundRunner,
)
from api.dependencies.session import CurrentContext
from shared import PermissionDeniedError

router = APIRouter()


class PlaygroundMessageIn(BaseModel):
    role: str
    content: str


class PlaygroundStateModel(BaseModel):
    """Simulated lead state carried turn-to-turn by the client (dry-run)."""

    lead_score: int = 0
    lead_sentiment: str | None = None
    lead_name: str | None = None
    lead_email: str | None = None
    pipeline_stage: str | None = None
    booked: bool = False
    escalated: bool = False
    turn_count: int = 0


class PlaygroundTurnIn(BaseModel):
    history: list[PlaygroundMessageIn] = []
    user_message: str
    state: PlaygroundStateModel | None = None


class PlaygroundBubble(BaseModel):
    text: str
    delay_ms: int


class PlaygroundEvent(BaseModel):
    kind: str
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)


class PlaygroundTurnOut(BaseModel):
    reply_text: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    retrieved_chunks: list[dict[str, Any]]
    # Dry-run: the bot's simulated tool outcomes (`events`) supersede the raw
    # `actions` list, so the HTTP contract exposes events, not raw actions.
    bubbles: list[PlaygroundBubble] = Field(default_factory=list)
    typing_indicator: bool = False
    events: list[PlaygroundEvent] = Field(default_factory=list)
    state: PlaygroundStateModel


@router.post("/turn", response_model=PlaygroundTurnOut)
async def playground_turn(
    turn: PlaygroundTurnIn, request: Request, ctx: CurrentContext
) -> PlaygroundTurnOut:
    """UC-08 — sandbox turn. Dry-run: no WhatsApp send, no DB persist, no GHL."""
    if ctx.merchant_id is None:
        raise PermissionDeniedError(
            "Playground requires a merchant context", error_code="no_merchant_context"
        )
    runner: PlaygroundRunner = request.app.state.playground

    response = await runner.run(
        PlaygroundRequest(
            tenant_id=ctx.tenant_id,
            merchant_id=ctx.merchant_id,
            history=[PlaygroundMessage(role=m.role, content=m.content) for m in turn.history],
            user_message=turn.user_message,
            state=PlaygroundLeadState.from_dict(
                turn.state.model_dump() if turn.state is not None else None
            ),
        )
    )

    return PlaygroundTurnOut(
        reply_text=response.reply_text,
        model=response.model,
        tokens_in=response.tokens_in,
        tokens_out=response.tokens_out,
        latency_ms=response.latency_ms,
        retrieved_chunks=response.retrieved_chunks,
        bubbles=[PlaygroundBubble(**b) for b in response.bubbles],
        typing_indicator=response.typing_indicator,
        events=[PlaygroundEvent(**e) for e in response.events],
        state=PlaygroundStateModel(**response.state),
    )
