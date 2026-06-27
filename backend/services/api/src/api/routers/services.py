"""Servizi prenotabili, orari di apertura e chiusure eccezionali (UC-02).

Tutti gli endpoint sono merchant-scoped: un utente merchant vede solo i propri
dati, un admin agency può gestire qualunque merchant (stesso pattern di catalog).
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.dependencies.session import CurrentContext, DBSession
from config_resolver import ConfigKey, ConfigResolver
from db import GHLMarketplaceRepository, session_scope
from db.repositories.services import (
    BusinessClosureRepository,
    BusinessHourRepository,
    ServiceRepository,
)
from db.session import TenantContext
from integrations.ghl.calendar_sync import (
    from_ghl_date_overrides,
    from_ghl_open_hours,
    to_ghl_date_overrides,
    to_ghl_open_hours,
)
from integrations.ghl.client import GHLClient, GHLTokenBundle
from shared import NotFoundError, PermissionDeniedError, get_logger, get_settings

logger = get_logger(__name__)

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────


class ServiceIn(BaseModel):
    name: str = Field(max_length=200)
    handle: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    duration_min: int = Field(ge=5, le=480)
    buffer_min: int = Field(default=0, ge=0, le=120)
    price: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="EUR", max_length=3)
    ghl_calendar_id: str | None = Field(default=None, max_length=120)
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True


class ServiceOut(BaseModel):
    id: UUID
    name: str
    handle: str
    description: str | None
    duration_min: int
    buffer_min: int
    price: Decimal | None
    currency: str
    ghl_calendar_id: str | None
    sort_order: int
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}


class BusinessHourIn(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    is_open: bool = True
    open_time: datetime.time | None = None
    close_time: datetime.time | None = None
    break_start: datetime.time | None = None
    break_end: datetime.time | None = None


class BusinessHourOut(BaseModel):
    id: UUID
    day_of_week: int
    is_open: bool
    open_time: datetime.time | None
    close_time: datetime.time | None
    break_start: datetime.time | None
    break_end: datetime.time | None

    model_config = {"from_attributes": True}


class BusinessClosureIn(BaseModel):
    closed_on: datetime.date
    label: str | None = Field(default=None, max_length=200)


class BusinessClosureOut(BaseModel):
    id: UUID
    closed_on: datetime.date
    label: str | None
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class SyncResult(BaseModel):
    ghl_calendar_id: str
    hours_imported: int
    closures_imported: int


# ── Helpers ───────────────────────────────────────────────────────────────────


def _assert_scope(ctx: TenantContext, merchant_id: UUID) -> None:
    if ctx.merchant_id is not None and ctx.merchant_id != merchant_id:
        raise PermissionDeniedError(
            "Cannot act on another merchant", error_code="cross_merchant_access"
        )


async def _get_ghl_client(
    merchant_id: UUID,
    session: object,
) -> tuple[GHLClient, str] | None:
    """Restituisce (client, location_id) o None se la location non è configurata."""
    settings = get_settings()
    marketplace = GHLMarketplaceRepository(session, kek_base64=settings.integrations_kek_base64)  # type: ignore[arg-type]
    loc = await marketplace.resolve_location_by_merchant(merchant_id)
    if loc is None or not loc.access_token:
        return None

    async def _persist(bundle: GHLTokenBundle) -> None:
        if not bundle.location_id:
            return
        try:
            async with session_scope() as s:
                await GHLMarketplaceRepository(
                    s, kek_base64=settings.integrations_kek_base64
                ).set_location_token(
                    location_id=bundle.location_id,
                    access_token=bundle.access_token,
                    refresh_token=bundle.refresh_token,
                    expires_at=bundle.expires_at,
                )
        except Exception as exc:
            logger.error("services.token_persist_failed", error=str(exc))

    client = GHLClient(
        token_bundle=GHLTokenBundle(
            access_token=loc.access_token,
            refresh_token=loc.refresh_token,
            expires_at=loc.expires_at,
            location_id=loc.location_id,
        ),
        client_id=settings.ghl_client_id,
        client_secret=settings.ghl_client_secret,
        on_token_refresh=_persist,
    )
    return client, loc.location_id


def _slugify(name: str) -> str:
    keep = "".join(c if c.isalnum() else "-" for c in name.lower())
    slug = "-".join(filter(None, keep.split("-")))
    return slug[:120] or "servizio"


# ── Orari di apertura ─────────────────────────────────────────────────────────
# Definiti prima di /{service_id} per evitare ambiguità di routing.


@router.get("/{merchant_id}/hours", response_model=list[BusinessHourOut])
async def list_hours(
    merchant_id: UUID,
    session: DBSession,
    ctx: CurrentContext,
) -> list[BusinessHourOut]:
    _assert_scope(ctx, merchant_id)
    repo = BusinessHourRepository(session)
    rows = await repo.list(merchant_id)
    return [BusinessHourOut.model_validate(r) for r in rows]


@router.put("/{merchant_id}/hours", response_model=list[BusinessHourOut])
async def upsert_hours(
    merchant_id: UUID,
    payload: list[BusinessHourIn],
    session: DBSession,
    ctx: CurrentContext,
) -> list[BusinessHourOut]:
    _assert_scope(ctx, merchant_id)
    hours_repo = BusinessHourRepository(session)
    days = [d.model_dump() for d in payload]
    rows = await hours_repo.upsert_bulk(merchant_id, days)

    # Push sincrono a GHL — best effort: non fallisce la risposta se GHL è giù.
    try:
        result = await _get_ghl_client(merchant_id, session)
        if result is not None:
            client, _loc_id = result
            config = ConfigResolver(session)  # type: ignore[arg-type]
            calendar_id = await config.resolve(
                ConfigKey.BOOKING_DEFAULT_CALENDAR_ID, merchant_id=merchant_id
            )
            if calendar_id:
                closures_repo = BusinessClosureRepository(session)
                closures = await closures_repo.list(merchant_id)
                open_hours = to_ghl_open_hours(rows)
                date_overrides = to_ghl_date_overrides(closures)
                try:
                    await client.update_calendar_hours(
                        str(calendar_id),
                        open_hours=open_hours,
                        date_overrides=date_overrides,
                    )
                    logger.info(
                        "services.hours_pushed_to_ghl",
                        merchant_id=str(merchant_id),
                        calendar_id=str(calendar_id),
                    )
                finally:
                    await client.close()
    except Exception as exc:
        logger.warning(
            "services.ghl_push_failed",
            merchant_id=str(merchant_id),
            error=str(exc),
        )

    return [BusinessHourOut.model_validate(r) for r in rows]


@router.post("/{merchant_id}/sync-from-ghl", response_model=SyncResult)
async def sync_from_ghl(
    merchant_id: UUID,
    session: DBSession,
    ctx: CurrentContext,
) -> SyncResult:
    """Pull orari e chiusure dal calendario GHL e sovrascrive il DB locale."""
    _assert_scope(ctx, merchant_id)

    result = await _get_ghl_client(merchant_id, session)
    if result is None:
        raise PermissionDeniedError(
            "Nessuna integrazione GHL configurata per questo merchant",
            error_code="ghl_not_connected",
        )
    client, _loc_id = result

    config = ConfigResolver(session)  # type: ignore[arg-type]
    calendar_id = await config.resolve(
        ConfigKey.BOOKING_DEFAULT_CALENDAR_ID, merchant_id=merchant_id
    )
    if not calendar_id:
        await client.close()
        raise NotFoundError("Nessun calendario GHL di default configurato (booking.default_calendar_id)")

    try:
        calendar = await client.get_calendar(str(calendar_id))
    finally:
        await client.close()

    ghl_open_hours: list[dict] = calendar.get("openHours") or []
    ghl_date_overrides: list[dict] = calendar.get("dateOverrides") or []

    hour_dicts = from_ghl_open_hours(ghl_open_hours)
    closure_dicts = from_ghl_date_overrides(ghl_date_overrides)

    hours_repo = BusinessHourRepository(session)
    await hours_repo.upsert_bulk(merchant_id, hour_dicts)

    closures_repo = BusinessClosureRepository(session)
    import datetime as _dt
    today = _dt.date.today()
    existing = await closures_repo.list(merchant_id, from_date=today)
    existing_dates = {c.closed_on for c in existing}
    incoming_dates = {d["closed_on"] for d in closure_dicts}

    for c in existing:
        if c.closed_on not in incoming_dates:
            await closures_repo.delete(merchant_id, c.id)
    for d in closure_dicts:
        if d["closed_on"] not in existing_dates:
            await closures_repo.add(
                merchant_id=merchant_id,
                closed_on=d["closed_on"],
                label=d.get("label"),
            )

    logger.info(
        "services.synced_from_ghl",
        merchant_id=str(merchant_id),
        calendar_id=str(calendar_id),
        hours=len(hour_dicts),
        closures=len(closure_dicts),
    )
    return SyncResult(
        ghl_calendar_id=str(calendar_id),
        hours_imported=len(hour_dicts),
        closures_imported=len(closure_dicts),
    )


# ── Chiusure eccezionali ──────────────────────────────────────────────────────


@router.get("/{merchant_id}/closures", response_model=list[BusinessClosureOut])
async def list_closures(
    merchant_id: UUID,
    session: DBSession,
    ctx: CurrentContext,
) -> list[BusinessClosureOut]:
    _assert_scope(ctx, merchant_id)
    repo = BusinessClosureRepository(session)
    today = datetime.date.today()
    rows = await repo.list(merchant_id, from_date=today)
    return [BusinessClosureOut.model_validate(r) for r in rows]


@router.post("/{merchant_id}/closures", response_model=BusinessClosureOut, status_code=201)
async def add_closure(
    merchant_id: UUID,
    payload: BusinessClosureIn,
    session: DBSession,
    ctx: CurrentContext,
) -> BusinessClosureOut:
    _assert_scope(ctx, merchant_id)
    repo = BusinessClosureRepository(session)
    row = await repo.add(
        merchant_id=merchant_id,
        closed_on=payload.closed_on,
        label=payload.label,
    )
    return BusinessClosureOut.model_validate(row)


@router.delete("/{merchant_id}/closures/{closure_id}", status_code=204)
async def delete_closure(
    merchant_id: UUID,
    closure_id: UUID,
    session: DBSession,
    ctx: CurrentContext,
) -> None:
    _assert_scope(ctx, merchant_id)
    repo = BusinessClosureRepository(session)
    deleted = await repo.delete(merchant_id, closure_id)
    if not deleted:
        raise NotFoundError("Chiusura non trovata")


# ── Servizi ───────────────────────────────────────────────────────────────────


@router.get("/{merchant_id}", response_model=list[ServiceOut])
async def list_services(
    merchant_id: UUID,
    session: DBSession,
    ctx: CurrentContext,
) -> list[ServiceOut]:
    _assert_scope(ctx, merchant_id)
    repo = ServiceRepository(session)
    svcs = await repo.list(merchant_id, include_inactive=True)
    return [ServiceOut.model_validate(s) for s in svcs]


@router.post("/{merchant_id}", response_model=ServiceOut, status_code=201)
async def create_service(
    merchant_id: UUID,
    payload: ServiceIn,
    session: DBSession,
    ctx: CurrentContext,
) -> ServiceOut:
    _assert_scope(ctx, merchant_id)
    repo = ServiceRepository(session)
    handle = payload.handle or _slugify(payload.name)
    svc = await repo.create(
        merchant_id=merchant_id,
        name=payload.name,
        handle=handle,
        duration_min=payload.duration_min,
        buffer_min=payload.buffer_min,
        description=payload.description,
        price=payload.price,
        currency=payload.currency,
        ghl_calendar_id=payload.ghl_calendar_id,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    return ServiceOut.model_validate(svc)


@router.get("/{merchant_id}/{service_id}", response_model=ServiceOut)
async def get_service(
    merchant_id: UUID,
    service_id: UUID,
    session: DBSession,
    ctx: CurrentContext,
) -> ServiceOut:
    _assert_scope(ctx, merchant_id)
    repo = ServiceRepository(session)
    svc = await repo.get(merchant_id, service_id)
    if svc is None:
        raise NotFoundError("Servizio non trovato")
    return ServiceOut.model_validate(svc)


@router.put("/{merchant_id}/{service_id}", response_model=ServiceOut)
async def update_service(
    merchant_id: UUID,
    service_id: UUID,
    payload: ServiceIn,
    session: DBSession,
    ctx: CurrentContext,
) -> ServiceOut:
    _assert_scope(ctx, merchant_id)
    repo = ServiceRepository(session)
    svc = await repo.get(merchant_id, service_id)
    if svc is None:
        raise NotFoundError("Servizio non trovato")
    handle = payload.handle or _slugify(payload.name)
    svc = await repo.update(
        svc,
        name=payload.name,
        handle=handle,
        duration_min=payload.duration_min,
        buffer_min=payload.buffer_min,
        description=payload.description,
        price=payload.price,
        currency=payload.currency,
        ghl_calendar_id=payload.ghl_calendar_id,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    return ServiceOut.model_validate(svc)


@router.delete("/{merchant_id}/{service_id}", status_code=204)
async def delete_service(
    merchant_id: UUID,
    service_id: UUID,
    session: DBSession,
    ctx: CurrentContext,
) -> None:
    _assert_scope(ctx, merchant_id)
    repo = ServiceRepository(session)
    deleted = await repo.delete(merchant_id, service_id)
    if not deleted:
        raise NotFoundError("Servizio non trovato")
