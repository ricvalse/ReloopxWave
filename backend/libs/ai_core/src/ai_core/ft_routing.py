"""FtModelResolver — routes a tenant's conversations to its fine-tuned model.

Implements the router's `FtModelProvider` protocol. Two behaviours (spec 6.7):
  - Rollout via A/B: while an FT experiment (a running A/B with an "ft" arm) is
    active for the merchant, only the "ft" variant routes to the FT model; the
    other arm stays on the baseline so the comparison is real.
  - After rollout: with a deployed FT default and no running FT experiment, all
    conversations for the tenant use the FT model.

The decision is a pure function (`should_use_ft`) so it's unit-testable without
a database; the resolver just supplies the three booleans from Postgres.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from db import session_scope
from db.models import FTModel
from db.models.ab import ABExperiment
from shared import get_logger

logger = get_logger(__name__)

FT_VARIANT_ID = "ft"


def should_use_ft(
    *, has_deployed_ft: bool, ft_experiment_running: bool, variant_id: str | None
) -> bool:
    if not has_deployed_ft:
        return False
    if ft_experiment_running:
        return variant_id == FT_VARIANT_ID
    return True


class FtModelResolver:
    """Concrete FtModelProvider backed by the ft_models + ab_experiments tables."""

    async def get(
        self, tenant_id: UUID, merchant_id: UUID, variant_id: str | None
    ) -> str | None:
        async with session_scope() as session:
            provider_model_id = (
                await session.execute(
                    select(FTModel.provider_model_id)
                    .where(
                        FTModel.tenant_id == tenant_id,
                        FTModel.is_default.is_(True),
                        FTModel.status == "deployed",
                        FTModel.provider_model_id != "",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if not provider_model_id:
                return None

            ft_experiment_running = (
                await session.execute(
                    select(ABExperiment.id)
                    .where(
                        ABExperiment.merchant_id == merchant_id,
                        ABExperiment.status == "running",
                        ABExperiment.variants.contains([{"id": FT_VARIANT_ID}]),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none() is not None

        if should_use_ft(
            has_deployed_ft=True,
            ft_experiment_running=ft_experiment_running,
            variant_id=variant_id,
        ):
            return str(provider_model_id)
        return None
