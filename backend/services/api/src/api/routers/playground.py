from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from ai_core import (
    PlaygroundLeadState,
    PlaygroundMessage,
    PlaygroundRequest,
    PlaygroundRunner,
)
from api.dependencies.session import CurrentContext, DBSession
from config_resolver import ConfigKey, ConfigResolver
from db.models import BotConfig
from shared import PermissionDeniedError

router = APIRouter()

# Max length of `bot.system_prompt_additions` (mirrors BotConfigSchema field).
_SYSTEM_PROMPT_ADDITIONS_MAX = 4000
# Header that brackets the tester-applied rules inside the additions text, so a
# subsequent /apply replaces THIS block instead of stacking duplicates.
_RULES_HEADER = "Regole aggiuntive dal tester (playground):"


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
    # Ad-hoc hard/soft rules the tester adds on the fly. Applied to the system
    # prompt for THIS turn only (preview), never persisted. Use POST /apply to
    # save them as a config override.
    override_rules: list[str] | None = None


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
            override_rules=turn.override_rules,
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


# ---- Save the tester's rules as a config override (UC-08) -----------------


class PlaygroundApplyIn(BaseModel):
    """Persist the playground's ad-hoc rules as a merchant config override.

    Reuses the existing `bot.system_prompt_additions` override (rather than a
    new config key) so the live WhatsApp flow picks the rules up through the
    same cascade — what the tester previews is exactly what production runs.
    """

    rules: list[str] = Field(default_factory=list)


class PlaygroundApplyOut(BaseModel):
    applied: bool
    system_prompt_additions: str | None


def _merge_rules_into_additions(existing: str | None, rules: list[str]) -> str | None:
    """Fold the tester's rules into `system_prompt_additions`.

    Any prose the merchant already wrote is kept; a previously-applied rules
    block (bracketed by `_RULES_HEADER`) is replaced, not duplicated. Returns
    the merged text trimmed to the schema's max length, or None when nothing
    is left (no rules + no prior prose).
    """
    cleaned = [r.strip() for r in rules if r and r.strip()]

    base = existing or ""
    idx = base.find(_RULES_HEADER)
    if idx != -1:
        base = base[:idx]
    base = base.strip()

    if not cleaned:
        return base or None

    block = _RULES_HEADER + "\n" + "\n".join(f"- {r}" for r in cleaned)
    merged = f"{base}\n\n{block}" if base else block
    return merged[:_SYSTEM_PROMPT_ADDITIONS_MAX].strip()


@router.post("/apply", response_model=PlaygroundApplyOut)
async def playground_apply(
    payload: PlaygroundApplyIn, session: DBSession, ctx: CurrentContext
) -> PlaygroundApplyOut:
    """UC-08 — promote the tester's playground rules to a saved config override.

    Writes them into the merchant's `bot.system_prompt_additions` so the live
    bot starts following them (the playground previews the same prompt).
    """
    if ctx.merchant_id is None:
        raise PermissionDeniedError(
            "Playground requires a merchant context", error_code="no_merchant_context"
        )
    merchant_id = ctx.merchant_id

    row = (
        await session.execute(select(BotConfig).where(BotConfig.merchant_id == merchant_id))
    ).scalar_one_or_none()
    overrides: dict[str, Any] = dict(row.overrides or {}) if row else {}

    existing = overrides.get(ConfigKey.BOT_SYSTEM_PROMPT_ADDITIONS.value)
    merged = _merge_rules_into_additions(
        existing if isinstance(existing, str) else None, payload.rules
    )

    if merged is None:
        overrides.pop(ConfigKey.BOT_SYSTEM_PROMPT_ADDITIONS.value, None)
    else:
        overrides[ConfigKey.BOT_SYSTEM_PROMPT_ADDITIONS.value] = merged

    if row is None:
        row = BotConfig(merchant_id=merchant_id, overrides=overrides)
        session.add(row)
    else:
        row.overrides = overrides

    # Invalidate the cache so the new prompt takes effect immediately (~no TTL
    # wait), mirroring bot_config.update_overrides.
    await session.flush()
    await ConfigResolver(session).invalidate(merchant_id)

    return PlaygroundApplyOut(applied=True, system_prompt_additions=merged)
