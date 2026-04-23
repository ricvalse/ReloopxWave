"""UC-11 / UC-12 — analytics endpoints."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from api.dependencies.auth import require_role
from api.dependencies.session import CurrentContext, DBSession
from config_resolver import ConfigKey, ConfigResolver
from db import AnalyticsRepository
from integrations import SupabaseStorage
from shared import IntegrationError, PermissionDeniedError, get_logger, get_settings

router = APIRouter()
logger = get_logger(__name__)


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
            for r in sorted(ranking, key=lambda r: (r.conversion_rate, r.leads_total), reverse=True)
        ],
    )


class ExportRequest(BaseModel):
    since_days: int = Field(default=30, ge=1, le=365)


class ExportOut(BaseModel):
    export_id: UUID
    status: str


class ExportDownload(BaseModel):
    export_id: UUID
    signed_url: str
    expires_in_seconds: int


@router.post("/exports", response_model=ExportOut, status_code=202)
async def request_export(
    payload: ExportRequest, ctx: CurrentContext, request: Request
) -> ExportOut:
    """Enqueue a background CSV export for the caller's tenant.

    Returns immediately with an `export_id`. Poll `GET /analytics/exports/{id}/download`
    for a signed Supabase Storage URL once the worker finishes. Large tenants may
    take a minute; Supabase returns 404 on the signed URL until the file exists.
    """
    export_id = uuid4()
    arq = request.app.state.arq
    await arq.enqueue_job(
        "build_analytics_export",
        str(ctx.tenant_id),
        str(export_id),
        since_days=payload.since_days,
        _job_id=f"analytics:export:{export_id}",
    )
    logger.info(
        "analytics.export.requested",
        actor_id=str(ctx.actor_id),
        tenant_id=str(ctx.tenant_id),
        export_id=str(export_id),
        since_days=payload.since_days,
    )
    return ExportOut(export_id=export_id, status="pending")


@router.get("/exports/{export_id}/download", response_model=ExportDownload)
async def download_export(export_id: UUID, ctx: CurrentContext) -> ExportDownload:
    """Return a signed URL for the export CSV if it's ready.

    Raises 404 with a domain error if the worker hasn't produced the file yet —
    that's the canonical "still pending" signal.
    """
    settings = get_settings()
    storage = SupabaseStorage(
        project_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
        bucket=settings.supabase_exports_bucket,
    )
    path = f"{ctx.tenant_id}/{export_id}.csv"
    expires = 3600
    try:
        signed = await storage.create_signed_url(path, expires_in_seconds=expires)
    except IntegrationError:
        # Supabase returns 4xx when the object doesn't exist yet; surface that
        # as "still pending" rather than a generic integration failure.
        raise IntegrationError(
            "Export not ready yet",
            error_code="export_not_ready",
            export_id=str(export_id),
        ) from None
    return ExportDownload(export_id=export_id, signed_url=signed, expires_in_seconds=expires)
