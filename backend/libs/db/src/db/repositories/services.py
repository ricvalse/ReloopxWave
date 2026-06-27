"""Repository per servizi prenotabili, orari di apertura e chiusure eccezionali."""

from __future__ import annotations

import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.services import BusinessClosure, BusinessHour, Service


class ServiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, merchant_id: UUID, *, include_inactive: bool = False) -> list[Service]:
        stmt = select(Service).where(Service.merchant_id == merchant_id)
        if not include_inactive:
            stmt = stmt.where(Service.is_active.is_(True))
        stmt = stmt.order_by(Service.sort_order, Service.name)
        return list((await self._session.execute(stmt)).scalars())

    async def get(self, merchant_id: UUID, service_id: UUID) -> Service | None:
        svc = await self._session.get(Service, service_id)
        if svc is None or svc.merchant_id != merchant_id:
            return None
        return svc

    async def create(
        self,
        *,
        merchant_id: UUID,
        name: str,
        handle: str,
        duration_min: int,
        buffer_min: int = 0,
        description: str | None = None,
        price: object | None = None,
        currency: str = "EUR",
        ghl_calendar_id: str | None = None,
        sort_order: int = 0,
        is_active: bool = True,
    ) -> Service:
        svc = Service(
            merchant_id=merchant_id,
            name=name,
            handle=handle,
            duration_min=duration_min,
            buffer_min=buffer_min,
            description=description,
            price=price,
            currency=currency,
            ghl_calendar_id=ghl_calendar_id,
            sort_order=sort_order,
            is_active=is_active,
        )
        self._session.add(svc)
        await self._session.flush()
        return svc

    async def update(self, svc: Service, **fields: object) -> Service:
        for k, v in fields.items():
            setattr(svc, k, v)
        await self._session.flush()
        return svc

    async def delete(self, merchant_id: UUID, service_id: UUID) -> bool:
        svc = await self.get(merchant_id, service_id)
        if svc is None:
            return False
        await self._session.delete(svc)
        await self._session.flush()
        return True


class BusinessHourRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, merchant_id: UUID) -> list[BusinessHour]:
        stmt = (
            select(BusinessHour)
            .where(BusinessHour.merchant_id == merchant_id)
            .order_by(BusinessHour.day_of_week)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def upsert_bulk(
        self,
        merchant_id: UUID,
        days: list[dict[str, object]],
    ) -> list[BusinessHour]:
        """Sostituisce tutti gli orari del merchant con i dati forniti.

        `days` è una lista di dict con chiavi: day_of_week, is_open, open_time,
        close_time, break_start, break_end. I giorni non inclusi vengono cancellati.
        """
        # Cancella righe esistenti e reinserisce — più semplice di un UPSERT multi-riga.
        await self._session.execute(
            delete(BusinessHour).where(BusinessHour.merchant_id == merchant_id)
        )
        rows: list[BusinessHour] = []
        for d in days:
            row = BusinessHour(
                merchant_id=merchant_id,
                day_of_week=d["day_of_week"],
                is_open=d.get("is_open", True),
                open_time=d.get("open_time"),
                close_time=d.get("close_time"),
                break_start=d.get("break_start"),
                break_end=d.get("break_end"),
            )
            self._session.add(row)
            rows.append(row)
        await self._session.flush()
        return rows


class BusinessClosureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self,
        merchant_id: UUID,
        *,
        from_date: datetime.date | None = None,
    ) -> list[BusinessClosure]:
        stmt = select(BusinessClosure).where(BusinessClosure.merchant_id == merchant_id)
        if from_date is not None:
            stmt = stmt.where(BusinessClosure.closed_on >= from_date)
        stmt = stmt.order_by(BusinessClosure.closed_on)
        return list((await self._session.execute(stmt)).scalars())

    async def add(
        self,
        *,
        merchant_id: UUID,
        closed_on: datetime.date,
        label: str | None = None,
    ) -> BusinessClosure:
        row = BusinessClosure(merchant_id=merchant_id, closed_on=closed_on, label=label)
        self._session.add(row)
        await self._session.flush()
        return row

    async def delete(self, merchant_id: UUID, closure_id: UUID) -> bool:
        row = await self._session.get(BusinessClosure, closure_id)
        if row is None or row.merchant_id != merchant_id:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
