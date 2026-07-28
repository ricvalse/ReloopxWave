"""UC-11 / UC-12 — analytics endpoints."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.dependencies.auth import require_role
from api.dependencies.session import CurrentContext, DBSession
from config_resolver import (
    ConfigKey,
    ConfigResolver,
    DashboardConfig,
    MetricDefinitionSchema,
)
from db import (
    AnalyticsRepository,
    MerchantRepository,
    MessageFilter,
    OutcomeRepository,
    StatsRepository,
    event_catalog,
)
from db.models import AnalyticsEvent, Objection
from integrations import SupabaseStorage
from shared import (
    IntegrationError,
    NotFoundError,
    PermissionDeniedError,
    get_logger,
    get_settings,
)

router = APIRouter()
logger = get_logger(__name__)

_MERCHANT_FILTER: Any = Query(default=None, description="Admin-only: target merchant_id")
# Le due dimensioni di attribuzione introdotte da 0047: restringono ogni bolla
# della pagina Statistiche al profilo e/o alla campagna selezionati.
_PROFILE_FILTER: Any = Query(
    default=None, description="Restringe ogni bolla al profilo di conversazione indicato"
)
_AUTOMATION_FILTER: Any = Query(
    default=None, description="Restringe le bolle a una singola automazione (campagna)"
)


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
    campaign: str | None = Query(default=None, description="Filter lead metrics to a campaign"),
    merchant_id: UUID | None = _MERCHANT_FILTER,
) -> MerchantKpisOut:
    target = _resolve_kpi_merchant(ctx, merchant_id)

    if ctx.role.startswith("agency"):
        # RLS already restricts cross-tenant reads, but an explicit lookup
        # gives a crisp 404 to the admin UI when the merchant_id is bogus.
        merchant = await MerchantRepository(session).get(target)
        if merchant is None or merchant.tenant_id != ctx.tenant_id:
            raise NotFoundError("Merchant not found", merchant_id=str(target))

    repo = AnalyticsRepository(session)
    config = ConfigResolver(session)
    hot = await config.resolve(ConfigKey.SCORING_HOT_THRESHOLD, merchant_id=target)
    hot_threshold = int(hot) if isinstance(hot, int) else 80

    k = await repo.merchant_kpis(
        merchant_id=target,
        hot_threshold=hot_threshold,
        since_days=since_days,
        campaign=campaign,
    )
    dist = await repo.score_distribution(merchant_id=target, campaign=campaign)
    return MerchantKpisOut(**asdict(k), score_distribution=dist)


@router.get("/merchant/campaigns", response_model=list[str])
async def merchant_campaigns(
    ctx: CurrentContext,
    session: DBSession,
    merchant_id: UUID | None = _MERCHANT_FILTER,
) -> list[str]:
    """Distinct campaigns for the merchant — populates the dashboard filter (UC-11)."""
    target = _resolve_kpi_merchant(ctx, merchant_id)
    campaigns: list[str] = await AnalyticsRepository(session).list_campaigns(merchant_id=target)
    return campaigns


def _resolve_kpi_merchant(ctx: CurrentContext, override: UUID | None) -> UUID:
    """Same shape as `_resolve_status_merchant` in routers/integrations.py:
    merchant users always see their own KPIs; agency callers must specify
    `?merchant_id=<uuid>` so they can inspect any merchant in their tenant.
    """
    if ctx.merchant_id is not None:
        if override is not None and override != ctx.merchant_id:
            raise PermissionDeniedError(
                "Cannot inspect another merchant's KPIs",
                error_code="cross_merchant_kpis",
            )
        return ctx.merchant_id
    if override is None:
        raise PermissionDeniedError(
            "Agency callers must specify merchant_id",
            error_code="missing_merchant_id",
        )
    return override


@router.get(
    "/agency/kpis",
    response_model=AgencyKpisOut,
    dependencies=[Depends(require_role("agency_admin"))],
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
                "merchant_name": r.merchant_name,
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


# ---- Catalogo eventi (vocabolario per la dashboard configurabile) ---------


class EventCatalogEntryOut(BaseModel):
    event_type: str
    label: str
    description: str
    category: str
    subject_type: str | None
    selectable: bool


@router.get("/event-catalog", response_model=list[EventCatalogEntryOut])
async def event_catalog_list(
    ctx: CurrentContext,
    selectable_only: bool = Query(
        default=False, description="Escludi eventi operativi/sintetici (rollup, log di sistema)"
    ),
) -> list[EventCatalogEntryOut]:
    """Catalogo tipato degli `event_type` che il sistema sa emettere.

    Single source of truth (ADR 0021) che popola il metric-builder della
    dashboard configurabile: il FE mostra `label`/`description` e usa
    `event_type` come chiave della metrica. `selectable_only=true` restituisce
    solo gli eventi di business (esclude rollup interni e log di sistema).
    """
    return [
        EventCatalogEntryOut(
            event_type=d.event_type.value,
            label=d.label,
            description=d.description,
            category=d.category.value,
            subject_type=d.subject_type,
            selectable=d.selectable,
        )
        for d in event_catalog(selectable_only=selectable_only)
    ]


# ---- Dashboard configurabile (metriche event-based) -----------------------


class MetricValueOut(BaseModel):
    id: str
    label: str
    source: str
    window_days: int
    value: int
    # Riferimento risolto della metrica, per il FE (quale evento / quale esito).
    event_type: str | None = None
    outcome_id: str | None = None
    # Solo per le bolle `outcome`: quanti dei conteggiati vengono da una sorgente
    # certa (webhook / marcatura umana) invece che da un'inferenza dell'LLM.
    # Permette alla bolla di dire "312, di cui 180 verificati" invece di
    # presentare un numero inferito come se fosse un fatto.
    verified: int | None = None


class MetricsOut(BaseModel):
    since_days: int
    profile_id: UUID | None = None
    metrics: list[MetricValueOut]


def group_metrics_by_window(
    definitions: Sequence[MetricDefinitionSchema], *, since_days: int
) -> dict[int, list[MetricDefinitionSchema]]:
    """Raggruppa le metriche per finestra effettiva → una query per finestra.

    `window_days=None` eredita la finestra globale della dashboard, quindi nel
    caso comune tutte le metriche cadono in un solo gruppo (una sola query).
    """
    by_window: dict[int, list[MetricDefinitionSchema]] = {}
    for d in definitions:
        by_window.setdefault(d.window_days or since_days, []).append(d)
    return by_window


def assemble_metric_values(
    definitions: Sequence[MetricDefinitionSchema],
    counts_by_window: dict[int, dict[str, int]],
    *,
    since_days: int,
) -> list[MetricValueOut]:
    """Mappa i conteggi delle metriche `event` sulle definizioni.

    Un event_type senza righe nella finestra vale 0 (non sparisce dalla
    dashboard): una metrica a zero è un'informazione, un buco è un bug.
    """
    values: list[MetricValueOut] = []
    for d in definitions:
        window = d.window_days or since_days
        counts = counts_by_window.get(window, {})
        values.append(
            MetricValueOut(
                id=d.id,
                label=d.label,
                source="event",
                event_type=d.event_type,
                window_days=window,
                value=int(counts.get(d.event_type or "", 0)),
            )
        )
    return values


async def _value_for_messages_metric(
    stats: StatsRepository,
    d: MetricDefinitionSchema,
    *,
    merchant_id: UUID,
    since: datetime,
    profile_id: UUID | None,
    automation_id: UUID | None,
) -> int:
    filters = MessageFilter(
        direction=d.direction,
        sender_types=tuple(d.sender_types),
        automation_id=automation_id,
        automation_node_key=d.automation_node_key,
        profile_id=profile_id,
        has_reply=d.has_reply,
    )
    if d.aggregation == "count_unique":
        return await stats.count_distinct_conversations(
            merchant_id=merchant_id, since=since, filters=filters
        )
    return await stats.count_messages(merchant_id=merchant_id, since=since, filters=filters)


@router.get("/metrics", response_model=MetricsOut)
async def merchant_metrics(
    ctx: CurrentContext,
    session: DBSession,
    since_days: int = Query(30, ge=1, le=365),
    merchant_id: UUID | None = _MERCHANT_FILTER,
    profile_id: UUID | None = _PROFILE_FILTER,
    automation_id: UUID | None = _AUTOMATION_FILTER,
) -> MetricsOut:
    """Calcola le bolle configurate per il merchant (ADR 0021 + 0047).

    Le definizioni vengono dal config cascade (`dashboard.metrics`). Con
    `profile_id` la risoluzione passa per il **livello 0** della cascata, quindi
    un profilo può avere un proprio set di bolle e non solo dati filtrati: è così
    che la pagina "divisa per profili" funziona su due assi — *quali* bolle vedi,
    e *su quali dati*.

    Le tre sorgenti hanno tre query diverse: `event` conta `analytics_events`
    (una query per finestra), `messages` conta i messaggi con filtri strutturali,
    `outcome` conta i fatti in `lead_outcomes`.
    """
    target = _resolve_kpi_merchant(ctx, merchant_id)

    if ctx.role.startswith("agency"):
        merchant = await MerchantRepository(session).get(target)
        if merchant is None or merchant.tenant_id != ctx.tenant_id:
            raise NotFoundError("Merchant not found", merchant_id=str(target))

    raw = await ConfigResolver(session).resolve(
        ConfigKey.DASHBOARD_METRICS, merchant_id=target, profile_id=profile_id
    )
    definitions = DashboardConfig(metrics=raw).metrics if raw else DashboardConfig().metrics

    now = datetime.now(tz=UTC)
    event_defs = [d for d in definitions if d.source == "event"]
    message_defs = [d for d in definitions if d.source == "messages"]
    outcome_defs = [d for d in definitions if d.source == "outcome"]

    # --- event: una query per finestra distinta (nel caso comune, una sola) ---
    repo = AnalyticsRepository(session)
    counts_by_window: dict[int, dict[str, int]] = {}
    for window, defs in group_metrics_by_window(event_defs, since_days=since_days).items():
        counts_by_window[window] = await repo.event_counts(
            merchant_id=target,
            since=now - timedelta(days=window),
            event_types=[d.event_type for d in defs if d.event_type],
            profile_id=profile_id,
            automation_id=automation_id,
        )
    by_id = {
        v.id: v
        for v in assemble_metric_values(event_defs, counts_by_window, since_days=since_days)
    }

    # --- messages: una query per bolla (filtri strutturali diversi) ----------
    stats = StatsRepository(session)
    for d in message_defs:
        window = d.window_days or since_days
        by_id[d.id] = MetricValueOut(
            id=d.id,
            label=d.label,
            source="messages",
            window_days=window,
            value=await _value_for_messages_metric(
                stats,
                d,
                merchant_id=target,
                since=now - timedelta(days=window),
                profile_id=profile_id,
                automation_id=automation_id,
            ),
        )

    # --- outcome: una query per finestra, raggruppata per esito --------------
    outcomes = OutcomeRepository(session)
    for window, defs in group_metrics_by_window(outcome_defs, since_days=since_days).items():
        wanted = [UUID(d.outcome_id) for d in defs if d.outcome_id]
        counts = await outcomes.count_by_outcome(
            merchant_id=target,
            since=now - timedelta(days=window),
            outcome_ids=wanted,
            profile_id=profile_id,
            automation_id=automation_id,
        )
        for d in defs:
            row = counts.get(UUID(d.outcome_id)) if d.outcome_id else None
            by_id[d.id] = MetricValueOut(
                id=d.id,
                label=d.label,
                source="outcome",
                outcome_id=d.outcome_id,
                window_days=window,
                value=row.total if row else 0,
                verified=row.verified if row else 0,
            )

    # Ordine configurato, non ordine di calcolo.
    return MetricsOut(
        since_days=since_days,
        profile_id=profile_id,
        metrics=[by_id[d.id] for d in definitions if d.id in by_id],
    )


# ---- Activity feed (timeline per lead) ------------------------------------


class AnalyticsEventOut(BaseModel):
    id: UUID
    event_type: str
    subject_type: str | None
    subject_id: UUID | None
    properties: dict[str, Any]
    occurred_at: Any


class AnalyticsEventsOut(BaseModel):
    events: list[AnalyticsEventOut]


@router.get("/events", response_model=AnalyticsEventsOut)
async def list_events(
    ctx: CurrentContext,
    session: DBSession,
    lead_id: UUID | None = Query(default=None, description="Filter by lead"),  # noqa: B008
    merchant_id: UUID | None = _MERCHANT_FILTER,
    since_days: int = Query(default=90, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=500),
) -> AnalyticsEventsOut:
    """Timeline degli analytics_events per lead o per merchant (uso interno + UI)."""
    target = _resolve_kpi_merchant(ctx, merchant_id)
    since = datetime.now(tz=UTC) - timedelta(days=since_days)

    stmt = (
        select(AnalyticsEvent)
        .where(AnalyticsEvent.merchant_id == target)
        .where(AnalyticsEvent.occurred_at >= since)
    )
    if lead_id is not None:
        stmt = stmt.where(AnalyticsEvent.subject_id == lead_id)

    stmt = stmt.order_by(AnalyticsEvent.occurred_at.desc()).limit(limit)
    rows = (await session.scalars(stmt)).all()

    return AnalyticsEventsOut(
        events=[
            AnalyticsEventOut(
                id=r.id,
                event_type=r.event_type,
                subject_type=r.subject_type,
                subject_id=r.subject_id,
                properties=r.properties or {},
                occurred_at=r.occurred_at,
            )
            for r in rows
        ]
    )


# S-08: objection trend analytics


class ObjectionTrendOut(BaseModel):
    category: str
    count_current_week: int
    count_prior_week: int
    growth_pct: float
    is_trending: bool
    suggested_rebuttal: str | None = None


@router.get(
    "/merchant/objection-trends",
    response_model=list[ObjectionTrendOut],
    dependencies=[Depends(require_role({"merchant_admin", "merchant_user", "tenant_admin"}))],
)
async def objection_trends(
    ctx: CurrentContext,
    session: DBSession,
    merchant_id: UUID | None = _MERCHANT_FILTER,
    with_suggestions: bool = Query(default=False, description="Generate LLM rebuttals for trending categories"),
) -> list[ObjectionTrendOut]:
    """Return objection category counts for the current vs prior 7 days.

    Pass `with_suggestions=true` to get LLM-generated rebuttal scripts for
    trending categories (uses one LLM call per trending category).
    """
    from collections import defaultdict

    from ai_core.llm import OpenAIClient
    from ai_core.objection_trends import compute_trends, suggest_rebuttal

    target = _resolve_kpi_merchant(ctx, merchant_id)
    now = datetime.now(tz=UTC)
    current_start = now - timedelta(days=7)
    prior_start = now - timedelta(days=14)

    # Fetch current week objections
    curr_rows = (
        await session.execute(
            select(Objection.category, Objection.quote)
            .where(
                Objection.merchant_id == target,
                Objection.created_at >= current_start,
            )
        )
    ).all()

    # Fetch prior week objections
    prior_rows = (
        await session.execute(
            select(Objection.category)
            .where(
                Objection.merchant_id == target,
                Objection.created_at >= prior_start,
                Objection.created_at < current_start,
            )
        )
    ).all()

    current_week: dict[str, int] = defaultdict(int)
    category_quotes: dict[str, list[str]] = defaultdict(list)
    for cat, quote in curr_rows:
        current_week[cat] += 1
        if quote:
            category_quotes[cat].append(quote)

    prior_week: dict[str, int] = defaultdict(int)
    for (cat,) in prior_rows:
        prior_week[cat] += 1

    trends = compute_trends(current_week=dict(current_week), prior_week=dict(prior_week))

    if with_suggestions:
        settings = get_settings()
        if settings.openai_api_key:
            client = OpenAIClient(api_key=settings.openai_api_key, model="gpt-4.1-mini")
            for t in trends:
                if t.is_trending:
                    t.suggested_rebuttal = await suggest_rebuttal(
                        client,
                        category=t.category,
                        sample_quotes=category_quotes.get(t.category),
                    )

    return [ObjectionTrendOut(**t.__dict__) for t in trends]


# S-10: predictive lead scoring


class PredictiveLeadScoreOut(BaseModel):
    lead_id: UUID
    probability: int
    dominant_feature: str
    effective_score: float | None
    sentiment: str | None


@router.get(
    "/merchant/lead-scores",
    response_model=list[PredictiveLeadScoreOut],
    dependencies=[Depends(require_role({"merchant_admin", "merchant_user", "tenant_admin"}))],
)
async def predictive_lead_scores(
    ctx: CurrentContext,
    session: DBSession,
    merchant_id: UUID | None = _MERCHANT_FILTER,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[PredictiveLeadScoreOut]:
    """Return predictive booking-probability scores for the top leads.

    Scores are computed server-side from the accumulated behavioral and content
    signals. Higher probability → higher chance of converting to a booking.
    """
    from ai_core.predictive_scoring import compute_booking_probability
    from db.models import Conversation, Lead

    target = _resolve_kpi_merchant(ctx, merchant_id)

    # Fetch leads with any engagement (have at least one conversation)
    subq = select(Conversation.lead_id).where(Conversation.merchant_id == target).distinct()
    leads = (
        await session.execute(
            select(Lead)
            .where(Lead.merchant_id == target, Lead.id.in_(subq), Lead.status != "erased")
            .order_by(Lead.score.desc())
            .limit(limit * 2)  # over-fetch; predictive sort may reorder
        )
    ).scalars().all()

    results: list[PredictiveLeadScoreOut] = []
    for lead in leads:
        content_signals: dict[str, bool] = (lead.meta or {}).get("content_signals", {})
        # Approximate turn_count from score_reasons (not perfect but avoids extra query).
        turn_count = max(len(lead.score_reasons or []), 1)

        ps = compute_booking_probability(
            content_signals=content_signals,
            effective_score=lead.effective_score,
            sentiment=lead.sentiment,
            avg_response_latency_seconds=lead.avg_response_latency_seconds,
            intake_score=lead.intake_score,
            turn_count=turn_count,
            was_read=lead.read_receipt_ratio,
            velocity_flag=lead.velocity_flag,
        )
        results.append(
            PredictiveLeadScoreOut(
                lead_id=lead.id,
                probability=ps.probability,
                dominant_feature=ps.dominant_feature,
                effective_score=lead.effective_score,
                sentiment=lead.sentiment,
            )
        )

    # Sort by predictive probability descending, trim to requested limit.
    results.sort(key=lambda r: r.probability, reverse=True)
    return results[:limit]
