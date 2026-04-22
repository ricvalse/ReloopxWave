"""Scheduled jobs — UC-03, UC-06, UC-13 + KPI rollup + KB reindex.

Cadence is configured per-merchant via the config cascade (section 9.4).
The handlers here are thin facades; real logic lives in dedicated modules so
ARQ's registration surface stays ergonomic.
"""
from __future__ import annotations

from shared import get_logger
from workers.scheduler.no_answer import followup_no_answer  # re-export for ARQ registration
from workers.scheduler.reactivation import reactivate_dormant_leads  # re-export

logger = get_logger(__name__)

__all__ = [
    "daily_kpi_rollup",
    "followup_no_answer",
    "kb_reindex",
    "objection_extraction",
    "reactivate_dormant_leads",
]


async def daily_kpi_rollup(ctx: dict) -> dict:
    """Aggregate analytics_events into pre-computed KPI tables for dashboards."""
    logger.info("worker.daily_kpi_rollup.started")
    raise NotImplementedError


async def objection_extraction(ctx: dict, conversation_id: str) -> dict:
    """UC-13 — run the objection classifier on a completed conversation.

    Real implementation lives in `workers.scheduler.objections`.
    """
    from workers.scheduler.objections import extract_for_conversation

    return await extract_for_conversation(ctx, conversation_id=conversation_id)


async def kb_reindex(ctx: dict, doc_id: str) -> dict:
    """Re-chunk and re-embed a KB doc after it changes.

    Real implementation lives in `workers.scheduler.kb_reindex`.
    """
    from workers.scheduler.kb_reindex import reindex_doc

    return await reindex_doc(ctx, doc_id=doc_id)
