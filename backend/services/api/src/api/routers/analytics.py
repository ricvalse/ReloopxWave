"""UC-11 / UC-12 — analytics endpoints."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.dependencies.auth import require_role
from api.dependencies.session import CurrentContext, DBSession
from config_resolver import ConfigKey, ConfigResolver
from db import AnalyticsRepository
from shared import PermissionDeniedError

router = APIRouter()


class MerchantKpisOut(BaseModel):
    leads_total: int
    leads_hot: int
    messages_received: int
    messages_replied: int
    response_rate: float
    bookings_created: int
    booking_rate: float
    reminders_sent: int
    score_distribution: list[dict[str, int]]


class AgencyKpisOut(BaseModel):
    leads_total: int
    active_merchants: int
    messages_received: int
    bookings_created: int
    reminders_sent: int
    merchants_ranking: list[dict[str, Any]]


@router.get("/merchant/kpis", response_model=MerchantKpisOut)
async def merchant_kpis(
    ctx: CurrentContext,
    session: DBSession,
    since_days: int = Query(30, ge=1, le=365),
) -> MerchantKpisOut:
    if ctx.merchant_id is None:
        raise PermissionDeniedError(
            "Merchant dashboard requires merchant context",
            error_code="no_merchant_context",
        )
    repo = AnalyticsRepository(session)
    config = ConfigResolver(session)
    hot = await config.resolve(ConfigKey.SCORING_HOT_THRESHOLD, merchant_id=ctx.merchant_id)
    hot_threshold = int(hot) if isinstance(hot, int) else 80

    k = await repo.merchant_kpis(
        merchant_id=ctx.merchant_id,
        hot_threshold=hot_threshold,
        since_days=since_days,
    )
    dist = await repo.score_distribution(merchant_id=ctx.merchant_id)
    return MerchantKpisOut(**k.__dict__, score_distribution=dist)


@router.get(
    "/agency/kpis",
    response_model=AgencyKpisOut,
    dependencies=[Depends(require_role("agency_admin", "agency_user"))],
)
async def agency_kpis(
    ctx: CurrentContext,
    session: DBSession,
    since_days: int = Query(30, ge=1, le=365),
) -> AgencyKpisOut:
    repo = AnalyticsRepository(session)
    totals = await repo.tenant_totals(tenant_id=ctx.tenant_id, since_days=since_days)
    ranking = await repo.merchants_ranking(tenant_id=ctx.tenant_id, since_days=since_days)
    return AgencyKpisOut(
        leads_total=totals["leads_total"],
        active_merchants=totals["active_merchants"],
        messages_received=totals["messages_received"],
        bookings_created=totals["bookings_created"],
        reminders_sent=totals["reminders_sent"],
        merchants_ranking=[
            {
                "merchant_id": str(r.merchant_id),
                "leads_total": r.leads_total,
                "bookings_created": r.bookings_created,
                "conversion_rate": r.conversion_rate,
            }
            for r in sorted(
                ranking, key=lambda r: (r.conversion_rate, r.leads_total), reverse=True
            )
        ],
    )


@router.post("/exports")
async def request_export(ctx: CurrentContext) -> dict:
    raise NotImplementedError("CSV export via Supabase Storage — follow-up")
