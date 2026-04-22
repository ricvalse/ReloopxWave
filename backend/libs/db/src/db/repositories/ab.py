"""A/B testing repository — experiments, deterministic variant assignment, metrics."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ABAssignment, ABExperiment, AnalyticsEvent


@dataclass(slots=True, frozen=True)
class VariantMetric:
    variant_id: str
    assignments: int
    events_by_type: dict[str, int]


class ABRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active_for_merchant(self, merchant_id: UUID) -> list[ABExperiment]:
        stmt = select(ABExperiment).where(
            ABExperiment.merchant_id == merchant_id, ABExperiment.status == "running"
        )
        return list((await self._session.execute(stmt)).scalars())

    async def get(self, experiment_id: UUID) -> ABExperiment | None:
        return await self._session.get(ABExperiment, experiment_id)

    async def create(
        self,
        *,
        merchant_id: UUID,
        name: str,
        description: str | None,
        variants: list[dict[str, Any]],
        primary_metric: str,
        min_sample_size: int = 100,
    ) -> ABExperiment:
        exp = ABExperiment(
            merchant_id=merchant_id,
            name=name,
            description=description,
            variants=variants,
            primary_metric=primary_metric,
            min_sample_size=min_sample_size,
            status="draft",
        )
        self._session.add(exp)
        await self._session.flush()
        return exp

    async def set_status(
        self, experiment_id: UUID, *, status: str, started_at: datetime | None = None
    ) -> None:
        exp = await self._session.get(ABExperiment, experiment_id)
        if exp is None:
            return
        exp.status = status
        if started_at is not None and exp.started_at is None:
            exp.started_at = started_at

    async def assign_variant(
        self,
        experiment: ABExperiment,
        *,
        lead_id: UUID,
        merchant_id: UUID,
    ) -> str:
        """Deterministic hash-based assignment. Re-calling this returns the same variant."""
        existing_stmt = select(ABAssignment).where(
            ABAssignment.experiment_id == experiment.id,
            ABAssignment.lead_id == lead_id,
        )
        existing = (await self._session.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            return existing.variant_id

        variants = experiment.variants or []
        if not variants:
            return "default"

        variant_id = _hash_pick(experiment_id=experiment.id, lead_id=lead_id, variants=variants)
        self._session.add(
            ABAssignment(
                experiment_id=experiment.id,
                merchant_id=merchant_id,
                lead_id=lead_id,
                variant_id=variant_id,
            )
        )
        await self._session.flush()
        return variant_id

    async def metrics(self, experiment_id: UUID) -> list[VariantMetric]:
        """Per-variant: number of lead assignments + counts of relevant events."""
        assignments_stmt = (
            select(ABAssignment.variant_id, func.count(ABAssignment.id))
            .where(ABAssignment.experiment_id == experiment_id)
            .group_by(ABAssignment.variant_id)
        )
        assignments = dict((await self._session.execute(assignments_stmt)).all())

        events_stmt = (
            select(
                AnalyticsEvent.variant_id,
                AnalyticsEvent.event_type,
                func.count(AnalyticsEvent.id),
            )
            .where(AnalyticsEvent.variant_id.in_(list(assignments.keys())))
            .group_by(AnalyticsEvent.variant_id, AnalyticsEvent.event_type)
        )
        events_rows = (await self._session.execute(events_stmt)).all()
        by_variant: dict[str, dict[str, int]] = {}
        for variant_id, event_type, count in events_rows:
            by_variant.setdefault(variant_id, {})[event_type] = int(count)

        return [
            VariantMetric(
                variant_id=variant_id,
                assignments=int(count),
                events_by_type=by_variant.get(variant_id, {}),
            )
            for variant_id, count in assignments.items()
        ]


def _hash_pick(*, experiment_id: UUID, lead_id: UUID, variants: list[dict[str, Any]]) -> str:
    """Cumulative-weight bucketing of a hash in [0, 100) across variant weights."""
    h = hashlib.sha256(f"{experiment_id}:{lead_id}".encode("utf-8")).digest()
    bucket = int.from_bytes(h[:4], "big") % 100  # [0, 99]

    total = sum(int(v.get("weight", 0)) for v in variants)
    if total <= 0:
        return str(variants[0].get("id", "default"))

    cursor = 0.0
    for v in variants:
        weight_pct = 100.0 * int(v.get("weight", 0)) / total
        cursor += weight_pct
        if bucket < cursor:
            return str(v.get("id", "default"))
    return str(variants[-1].get("id", "default"))
