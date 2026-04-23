"""Fine-tuning pipeline (section 5.4, weeks 9-10).

End-to-end path for one tenant:
    collect_training_pairs() → export_training_pairs() → fine_tune_train
    → fine_tune_evaluate → fine_tune_deploy (flip is_default)

V1 `fine_tune_evaluate` is intentionally modest: it records provider_model_id
+ status without running a held-out eval. A real eval — BLEU / objection
handling / latency comparison vs baseline — is explicit Phase 5.2 follow-up
work, tracked in docs/runbooks/fine-tune-deploy.md (not yet written).
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

logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 30
POLL_MAX_ATTEMPTS = 240  # 2 hours at 30s cadence — OpenAI FT rarely exceeds this.


async def fine_tune_train(
    ctx: dict[str, Any],
    tenant_id: str,
    *,
    dataset_path: str,
    base_model: str,
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
    """V1: record whatever evaluation signal we have and mark the row as
    evaluated. A meaningful held-out comparison lives in the follow-up runbook.
    """
    del test_set_path  # placeholder — see Phase 5.2 follow-up notes

    async with session_scope() as session:
        row = await session.get(FTModel, UUID(ft_model_row_id))
        if row is None or row.status != "ready":
            raise IntegrationError(
                "FT model not ready for evaluation",
                error_code="ft_not_ready",
                ft_model_row_id=ft_model_row_id,
            )
        row.evaluation = {
            **(row.evaluation or {}),
            "evaluated_at": datetime.now(tz=UTC).isoformat(),
            "method": "placeholder_v1",
            "pass": True,
        }
        row.status = "evaluated"

    logger.info("ft.evaluate.done", ft_model_row_id=ft_model_row_id)
    return {"ft_model_row_id": ft_model_row_id, "status": "evaluated"}


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
        if row.status not in ("ready", "evaluated"):
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
        deploy_payload = {
            "tenant_id": str(row.tenant_id),
            "ft_model_row_id": ft_model_row_id,
            "provider_model_id": row.provider_model_id,
        }

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
