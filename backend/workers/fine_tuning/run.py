"""FT pipeline orchestrator — the single entry point that runs the chain.

    fine_tune_run(tenant_id)
      → collect_training_pairs        (positive-outcome conversations)
      → quality.filter_pairs          (drop bot-errors / dropoffs / empties)
      → export_training_pairs         (anonymize regex+presidio, upload JSONL)
      → enqueue fine_tune_train       (OpenAI FT job + poll)
          → enqueue fine_tune_evaluate (held-out vs baseline, pass gate)
              → enqueue fine_tune_deploy (A/B rollout) on pass

This module owns the first three (synchronous, cheap) steps and then hands off
to the long-running train job via the queue. The train/evaluate/deploy steps
chain themselves (each enqueues the next) so the whole pipeline runs from one
trigger.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from db import TenantContext, tenant_session
from shared import IntegrationError, get_logger, get_settings
from workers.fine_tuning.collect import collect_training_pairs
from workers.fine_tuning.export import export_training_pairs
from workers.fine_tuning.quality import filter_pairs

logger = get_logger(__name__)

# Default FT base. The exact FT-able provider id is set per-deploy; this matches
# the spec's gpt-4.1-mini target (section 6.7).
DEFAULT_FT_BASE_MODEL = "gpt-4.1-mini"
DEFAULT_WINDOW_DAYS = 28  # "last 4 weeks" (spec 5.4 data_collector)
MIN_PAIRS_TO_TRAIN = 10


async def fine_tune_run(
    ctx: dict[str, Any],
    tenant_id: str,
    *,
    since_days: int = DEFAULT_WINDOW_DAYS,
    base_model: str = DEFAULT_FT_BASE_MODEL,
) -> dict[str, Any]:
    settings = get_settings()
    tid = UUID(tenant_id)
    until = datetime.now(tz=UTC)
    since = until - timedelta(days=since_days)

    # Run collect inside a tenant-scoped session so RLS stays a backstop on top
    # of the application-level tenant filter in collect.py: even if that filter
    # regressed, RLS would still confine rows to this tenant. `merchant_id=None`
    # spans the whole tenant (collect joins across all the tenant's merchants);
    # the worker role is downgraded inside `tenant_session` so policies apply.
    tenant_ctx = TenantContext(
        tenant_id=tid,
        merchant_id=None,
        role="worker",
        actor_id=tid,
    )
    async with tenant_session(tenant_ctx) as session:
        pairs = await collect_training_pairs(session, tenant_id=tid, since=since, until=until)

    quality = filter_pairs(pairs)
    logger.info(
        "ft.run.collected",
        tenant_id=tenant_id,
        collected=len(pairs),
        kept=len(quality.kept),
        dropped=quality.dropped,
        drop_reasons=quality.reasons,
    )

    if len(quality.kept) < MIN_PAIRS_TO_TRAIN:
        logger.warning(
            "ft.run.insufficient_data",
            tenant_id=tenant_id,
            kept=len(quality.kept),
            minimum=MIN_PAIRS_TO_TRAIN,
        )
        return {
            "tenant_id": tenant_id,
            "status": "insufficient_data",
            "kept": len(quality.kept),
            "minimum": MIN_PAIRS_TO_TRAIN,
        }

    export = await export_training_pairs(settings=settings, tenant_id=tid, pairs=quality.kept)

    redis = ctx.get("redis")
    if redis is None:  # pragma: no cover — redis always present in ARQ ctx
        raise IntegrationError("no redis in worker ctx", error_code="no_redis")
    await redis.enqueue_job(
        "fine_tune_train",
        tenant_id,
        dataset_path=export.path,
        base_model=base_model,
        _job_id=f"ft:train:{export.run_id}",
    )

    logger.info(
        "ft.run.enqueued_train",
        tenant_id=tenant_id,
        run_id=export.run_id,
        dataset_path=export.path,
        pairs=export.pairs_count,
    )
    return {
        "tenant_id": tenant_id,
        "status": "training_enqueued",
        "run_id": export.run_id,
        "dataset_path": export.path,
        "pairs": export.pairs_count,
        "redaction_totals": export.redaction_totals,
    }
