from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ai_core import PlaygroundMessage, PlaygroundRequest, PlaygroundRunner
from api.dependencies.session import CurrentContext
from shared import PermissionDeniedError

router = APIRouter()


class PlaygroundMessageIn(BaseModel):
    role: str
    content: str


class PlaygroundTurnIn(BaseModel):
    system_prompt: str
    history: list[PlaygroundMessageIn] = []
    user_message: str
    variant_id: str | None = None
    use_kb: bool = True


class PlaygroundTurnOut(BaseModel):
    reply_text: str
    actions: list[dict[str, Any]]
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    retrieved_chunks: list[dict[str, Any]]


@router.post("/turn", response_model=PlaygroundTurnOut)
async def playground_turn(
    turn: PlaygroundTurnIn, request: Request, ctx: CurrentContext
) -> PlaygroundTurnOut:
    """UC-08 — sandbox turn. No side-effects: no WhatsApp send, no DB persist."""
    if ctx.merchant_id is None:
        raise PermissionDeniedError(
            "Playground requires a merchant context", error_code="no_merchant_context"
        )
    runner: PlaygroundRunner = request.app.state.playground

    response = await runner.run(
        PlaygroundRequest(
            tenant_id=ctx.tenant_id,
            merchant_id=ctx.merchant_id,
            system_prompt=turn.system_prompt,
            history=[PlaygroundMessage(role=m.role, content=m.content) for m in turn.history],
            user_message=turn.user_message,
            variant_id=turn.variant_id,
            use_kb=turn.use_kb,
        )
    )

    return PlaygroundTurnOut(
        reply_text=response.reply_text,
        actions=response.actions,
        model=response.model,
        tokens_in=response.tokens_in,
        tokens_out=response.tokens_out,
        latency_ms=response.latency_ms,
        retrieved_chunks=response.retrieved_chunks,
    )
