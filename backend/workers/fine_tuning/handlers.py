"""Fine-tuning pipeline (section 5.4, weeks 9-10).

End-to-end path for one tenant:
    collect_training_pairs() → export_training_pairs() (train/eval split)
    → fine_tune_train → fine_tune_evaluate (held-out vs baseline)
    → fine_tune_deploy (A/B rollout)

`fine_tune_evaluate` (real impl in evaluate.py) runs a held-out evaluation
against the eval split: structured-output validity + booking-action recall vs
the baseline model. Senza held-out set lo stato diventa `eval_skipped`, escluso
dal gate di deploy (DEPLOYABLE_STATUSES). Quando l'FT è legato a un merchant,
il deploy apre un esperimento A/B baseline-vs-ft invece di flippare is_default.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update

from db import session_scope
from db.models import FTModel
from shared import IntegrationError, get_logger, get_settings
from workers.fine_tuning.run import fine_tune_run  # re-export for ARQ registration

logger = get_logger(__name__)

__all__ = [
    "fine_tune_deploy",
    "fine_tune_evaluate",
    "fine_tune_run",
    "fine_tune_train",
]

POLL_INTERVAL_SECONDS = 30
POLL_MAX_ATTEMPTS = 240  # 2 hours at 30s cadence — OpenAI FT rarely exceeds this.

# Stati da cui un FT model può essere promosso a default. Esclude esplicitamente
# 'eval_skipped'/'eval_failed' (#18): una valutazione mancante/fallita non deve
# poter scivolare in produzione.
DEPLOYABLE_STATUSES = ("ready", "evaluated")


async def fine_tune_train(
    ctx: dict[str, Any],
    tenant_id: str,
    *,
    dataset_path: str,
    base_model: str,
    eval_path: str | None = None,
    target_merchant_id: str | None = None,
) -> dict[str, Any]:
    """Submit an OpenAI fine-tuning job pointed at the uploaded dataset and
    poll until it either succeeds or fails. Persists an `ft_models` row in
    either case so operators can see what happened.

    `dataset_path` is the Supabase Storage path from `export_training_pairs`.
    For now we upload the file bytes to OpenAI Files (OpenAI's FT API doesn't
    accept a URL). A resumable / streaming variant is a future optimisation.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        raise IntegrationError(
            "OPENAI_API_KEY not configured",
            error_code="openai_not_configured",
        )

    try:
        from openai import AsyncOpenAI
    except ImportError as e:
        raise IntegrationError(
            "openai package not available",
            error_code="openai_dep_missing",
        ) from e

    from integrations import SupabaseStorage

    storage = SupabaseStorage(
        project_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
        bucket=settings.supabase_ft_bucket,
    )
    training_bytes = await storage.download(dataset_path)

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        upload = await client.files.create(
            file=("train.jsonl", training_bytes, "application/x-ndjson"),
            purpose="fine-tune",
        )
        job = await client.fine_tuning.jobs.create(
            training_file=upload.id,
            model=base_model,
        )

        logger.info(
            "ft.train.submitted",
            tenant_id=tenant_id,
            job_id=job.id,
            file_id=upload.id,
            base_model=base_model,
        )

        version = await _next_version(UUID(tenant_id))
        async with session_scope() as session:
            row = FTModel(
                tenant_id=UUID(tenant_id),
                # Legare l'FT a un merchant abilita il rollout A/B in deploy (#16);
                # senza merchant l'FT resta default tenant-wide.
                merchant_id=UUID(target_merchant_id) if target_merchant_id else None,
                version=version,
                base_model=base_model,
                provider_model_id="",
                dataset_path=dataset_path,
                training_job_id=job.id,
                status="training",
            )
            session.add(row)
            await session.flush()
            ft_model_id = row.id

        final_job = await _poll_to_terminal(client, job.id)
        final_status = "ready" if final_job.status == "succeeded" else "failed"
        provider_model_id = final_job.fine_tuned_model or ""

        async with session_scope() as session:
            await session.execute(
                update(FTModel)
                .where(FTModel.id == ft_model_id)
                .values(
                    status=final_status,
                    provider_model_id=provider_model_id,
                    trained_at=datetime.now(tz=UTC) if final_status == "ready" else None,
                )
            )

        logger.info(
            "ft.train.finished",
            tenant_id=tenant_id,
            job_id=job.id,
            status=final_status,
            provider_model_id=provider_model_id,
        )

        # Chain: on success, kick off held-out evaluation against the split set.
        if final_status == "ready":
            redis = ctx.get("redis")
            if redis is not None:
                await redis.enqueue_job(
                    "fine_tune_evaluate",
                    str(ft_model_id),
                    test_set_path=eval_path,
                    _job_id=f"ft:eval:{ft_model_id}",
                )

        return {
            "ft_model_id": str(ft_model_id),
            "job_id": job.id,
            "status": final_status,
            "provider_model_id": provider_model_id,
        }
    finally:
        await client.close()


async def fine_tune_evaluate(
    ctx: dict[str, Any],
    ft_model_row_id: str,
    *,
    test_set_path: str | None = None,
) -> dict[str, Any]:
    """Held-out evaluation of the FT model vs baseline (real impl in evaluate.py)."""
    from workers.fine_tuning.evaluate import evaluate_model

    return await evaluate_model(ctx, ft_model_row_id, test_set_path=test_set_path)


async def fine_tune_deploy(
    ctx: dict[str, Any],
    ft_model_row_id: str,
) -> dict[str, Any]:
    """Mark the given FT model as the tenant default. Clears `is_default` on
    any other row for the same tenant in the same transaction so ModelRouter
    only ever sees one live default.
    """
    async with session_scope() as session:
        row = await session.get(FTModel, UUID(ft_model_row_id))
        if row is None:
            raise IntegrationError(
                "FT model row not found",
                error_code="ft_row_missing",
                ft_model_row_id=ft_model_row_id,
            )
        # Stati deployabili: 'ready' (trainato, eval non ancora girata) e
        # 'evaluated' (eval passata). 'eval_skipped'/'eval_failed' restano fuori
        # dal gate (#18): un eval senza held-out set o fallito non vale come ok.
        if row.status not in DEPLOYABLE_STATUSES:
            raise IntegrationError(
                f"Cannot deploy FT model in status {row.status}",
                error_code="ft_not_deployable",
            )

        await session.execute(
            update(FTModel)
            .where(FTModel.tenant_id == row.tenant_id, FTModel.id != row.id)
            .values(is_default=False)
        )
        row.is_default = True
        row.status = "deployed"
        merchant_id = row.merchant_id
        deploy_payload: dict[str, str | None] = {
            "tenant_id": str(row.tenant_id),
            "ft_model_row_id": ft_model_row_id,
            "provider_model_id": row.provider_model_id,
        }

        # Rollout via A/B, not a flag flip (spec 6.7). When the FT model targets a
        # specific merchant, open a running baseline-vs-ft experiment; the
        # FtModelResolver then routes only the "ft" arm to the FT model until the
        # experiment is stopped. Tenant-wide FT (no merchant) just becomes the
        # default for all conversations (no gating experiment).
        experiment_id: str | None = None
        if merchant_id is not None:
            from db import ABRepository

            ab = ABRepository(session)
            # One running experiment per merchant (UC-09): variant assignment only
            # honours the oldest running experiment, so silently creating a second
            # one would hijack or starve traffic. Respect the same guard the API's
            # start_experiment enforces — if something is already running, skip the
            # FT experiment. The model still deploys via the is_default path above.
            if await ab.has_running(merchant_id):
                logger.warning(
                    "ft.deploy.ab_skipped_running_experiment",
                    tenant_id=str(row.tenant_id),
                    merchant_id=str(merchant_id),
                    ft_model_row_id=ft_model_row_id,
                )
            else:
                exp = await ab.create(
                    merchant_id=merchant_id,
                    name=f"FT rollout v{row.version}",
                    description="Auto-created baseline vs fine-tuned model comparison.",
                    variants=[
                        {"id": "baseline", "weight": 50},
                        {"id": "ft", "weight": 50},
                    ],
                    primary_metric="booking.created",
                    min_sample_size=100,
                )
                await ab.set_status(exp.id, status="running", started_at=datetime.now(tz=UTC))
                experiment_id = str(exp.id)

    deploy_payload["ab_experiment_id"] = experiment_id
    logger.info("ft.deploy.done", **deploy_payload)
    return deploy_payload


# ---- helpers ---------------------------------------------------------------


async def _next_version(tenant_id: UUID) -> int:
    async with session_scope() as session:
        current = (
            await session.execute(
                select(FTModel.version)
                .where(FTModel.tenant_id == tenant_id)
                .order_by(FTModel.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return (current or 0) + 1


async def _poll_to_terminal(client: Any, job_id: str) -> Any:
    """Block until the OpenAI FT job reaches a terminal status.

    ARQ handles worker-level timeouts, so we cap our own wait to keep the
    handler predictable. If we hit the cap, raise — ARQ will mark the job
    failed and the FT row stays in `training` until the next run.
    """
    for _ in range(POLL_MAX_ATTEMPTS):
        job = await client.fine_tuning.jobs.retrieve(job_id)
        if job.status in ("succeeded", "failed", "cancelled"):
            return job
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    raise IntegrationError(
        "OpenAI FT job did not reach terminal status within poll window",
        error_code="ft_poll_timeout",
        job_id=job_id,
    )
