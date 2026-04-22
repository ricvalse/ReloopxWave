"""UC-05 — update_score action handler.

The orchestrator emits `update_score` with a `signals` dict in the payload
(boolean flags matching `ai_core.scoring.SIGNAL_WEIGHTS`). This handler:
  1. Derives extra signals from conversation-level facts (engagement, timing).
  2. Combines with the LLM-reported signals.
  3. Runs `score_lead` and persists the result.
  4. Emits `lead_score_changed` when the score crosses the hot/cold threshold.

Keeping scoring as an action (vs. always-on post-turn hook) lets the LLM tell
us *why* a signal is present — the `reason_codes` then surface in the UI.
"""
from __future__ import annotations

from typing import Any

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

    async def __call__(self, action: OrchestratorAction, turn_ctx) -> None:
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
