"""UC-05 — update_score action handler.

Scoring is always-on: `ConversationService` derives behavioural signals from
cumulative conversation state (name/email on file, engagement, sentiment,
booking intent), merges them with any content signals the LLM reported this
turn, and injects a single `update_score` action carrying the merged `signals`
dict (boolean flags matching `ai_core.scoring.SIGNAL_WEIGHTS`). This handler:
  1. Whitelists the signals (defensive against hallucinated keys).
  2. Runs `score_lead` over the cumulative signal set and persists the result.
  3. Emits `lead_score_changed` with the previous/new hot/cold temperature.

Because the signals reflect accumulated state rather than only the current
message, the score is stable across turns — a single late negative turn no
longer craters an otherwise-hot lead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ai_core.orchestrator import OrchestratorAction
from ai_core.scoring import SIGNAL_WEIGHTS, score_lead
from config_resolver import ConfigKey, ConfigResolver
from db import (
    AnalyticsRepository,
    LeadRepository,
    TenantContext,
    tenant_session,
)
from shared import get_logger

if TYPE_CHECKING:
    from ai_core.conversation_service import TurnContext

logger = get_logger(__name__)


def derive_signals_from_llm_payload(payload: dict[str, Any]) -> dict[str, bool]:
    """Whitelist the booleans the LLM sent, ignore anything the scorer doesn't know.

    Defensive: we accept exactly the keys that map to weights in SIGNAL_WEIGHTS.
    This prevents model hallucinations from invisibly shifting scores.
    """
    signals = payload.get("signals") or {}
    return {k: bool(signals.get(k)) for k in SIGNAL_WEIGHTS if k in signals}


class UpdateScoreHandler:
    kind = "update_score"

    async def __call__(self, action: OrchestratorAction, turn_ctx: TurnContext) -> None:
        worker_ctx = TenantContext(
            tenant_id=turn_ctx.tenant_id,
            merchant_id=turn_ctx.merchant_id,
            role="worker",
            actor_id=turn_ctx.merchant_id,
        )

        signals = derive_signals_from_llm_payload(action.payload)
        if not signals:
            logger.debug("uc05.no_signals", conversation_id=str(turn_ctx.conversation_id))
            return

        async with tenant_session(worker_ctx) as session:
            leads = LeadRepository(session)
            analytics = AnalyticsRepository(session)
            config = ConfigResolver(session)

            lead = await leads.get_by_phone(
                merchant_id=turn_ctx.merchant_id, phone=turn_ctx.lead_phone
            )
            if lead is None:
                return

            scored = score_lead(signals)
            previous_score = lead.score
            await leads.update_score(lead.id, score=scored.score, reasons=scored.reason_codes)

            hot_threshold = _as_int(
                await config.resolve(
                    ConfigKey.SCORING_HOT_THRESHOLD, merchant_id=turn_ctx.merchant_id
                ),
                80,
            )
            cold_threshold = _as_int(
                await config.resolve(
                    ConfigKey.SCORING_COLD_THRESHOLD, merchant_id=turn_ctx.merchant_id
                ),
                30,
            )
            temperature = _classify(scored.score, hot_threshold, cold_threshold)
            previous_temp = _classify(previous_score, hot_threshold, cold_threshold)

            await analytics.emit(
                tenant_id=turn_ctx.tenant_id,
                merchant_id=turn_ctx.merchant_id,
                event_type="lead_score_changed",
                subject_type="lead",
                subject_id=turn_ctx.lead_id,
                properties={
                    "previous_score": previous_score,
                    "new_score": scored.score,
                    "temperature": temperature,
                    "previous_temperature": previous_temp,
                    "reason_codes": scored.reason_codes,
                    "signals": signals,
                    "conversation_id": str(turn_ctx.conversation_id),
                },
            )


def _classify(score: int, hot: int, cold: int) -> str:
    if score >= hot:
        return "hot"
    if score <= cold:
        return "cold"
    return "warm"


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default
