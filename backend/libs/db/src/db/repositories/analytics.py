from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AnalyticsEvent, Lead, Merchant


@dataclass(slots=True, frozen=True)
class MerchantKpis:
    leads_total: int
    leads_hot: int
    messages_received: int
    messages_replied: int
    response_rate: float
    bookings_created: int
    booking_rate: float
    reminders_sent: int


@dataclass(slots=True, frozen=True)
class MerchantRanking:
    merchant_id: UUID
    leads_total: int
    bookings_created: int
    conversion_rate: float


class AnalyticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def emit(
        self,
        *,
        tenant_id: UUID,
        merchant_id: UUID | None,
        event_type: str,
        subject_type: str | None = None,
        subject_id: UUID | None = None,
        variant_id: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> AnalyticsEvent:
        event = AnalyticsEvent(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            variant_id=variant_id,
            properties=properties or {},
        )
        self._session.add(event)
        await self._session.flush()
        return event

    # ---- UC-11 merchant dashboard ---------------------------------------

    async def merchant_kpis(
        self,
        *,
        merchant_id: UUID,
        hot_threshold: int = 80,
        since_days: int = 30,
    ) -> MerchantKpis:
        since = datetime.now(tz=UTC) - timedelta(days=since_days)

        leads_row = (
            await self._session.execute(
                select(
                    func.count(Lead.id),
                    func.sum(case((Lead.score >= hot_threshold, 1), else_=0)),
                ).where(Lead.merchant_id == merchant_id)
            )
        ).one()
        leads_total = int(leads_row[0] or 0)
        leads_hot = int(leads_row[1] or 0)

        counts = dict(
            (
                await self._session.execute(
                    select(AnalyticsEvent.event_type, func.count(AnalyticsEvent.id))
                    .where(
                        AnalyticsEvent.merchant_id == merchant_id,
                        AnalyticsEvent.occurred_at >= since,
                    )
                    .group_by(AnalyticsEvent.event_type)
                )
            ).all()
        )
        messages_received = int(counts.get("message.received", 0))
        messages_replied = int(counts.get("message.replied", 0))
        bookings_created = int(counts.get("booking.created", 0))
        reminders_sent = int(counts.get("reminder.sent", 0))

        response_rate = (messages_replied / messages_received) if messages_received else 0.0
        booking_rate = (bookings_created / leads_total) if leads_total else 0.0

        return MerchantKpis(
            leads_total=leads_total,
            leads_hot=leads_hot,
            messages_received=messages_received,
            messages_replied=messages_replied,
            response_rate=response_rate,
            bookings_created=bookings_created,
            booking_rate=booking_rate,
            reminders_sent=reminders_sent,
        )

    async def score_distribution(self, *, merchant_id: UUID) -> list[dict[str, int]]:
        expr = (func.floor(Lead.score / 10) * 10).label("bucket")
        rows = (
            await self._session.execute(
                select(expr, func.count(Lead.id))
                .where(Lead.merchant_id == merchant_id)
                .group_by("bucket")
                .order_by("bucket")
            )
        ).all()
        return [{"bucket": int(b), "count": int(c)} for b, c in rows]

    # ---- UC-12 agency dashboard -----------------------------------------

    async def tenant_totals(
        self, *, tenant_id: UUID, since_days: int = 30
    ) -> dict[str, int]:
        since = datetime.now(tz=UTC) - timedelta(days=since_days)
        counts = dict(
            (
                await self._session.execute(
                    select(AnalyticsEvent.event_type, func.count(AnalyticsEvent.id))
                    .where(
                        AnalyticsEvent.tenant_id == tenant_id,
                        AnalyticsEvent.occurred_at >= since,
                    )
                    .group_by(AnalyticsEvent.event_type)
                )
            ).all()
        )
        leads_total = int(
            (
                await self._session.execute(
                    select(func.count(Lead.id))
                    .join(Merchant, Merchant.id == Lead.merchant_id)
                    .where(Merchant.tenant_id == tenant_id)
                )
            ).scalar()
            or 0
        )
        active_merchants = int(
            (
                await self._session.execute(
                    select(func.count(Merchant.id))
                    .where(Merchant.tenant_id == tenant_id, Merchant.status == "active")
                )
            ).scalar()
            or 0
        )
        return {
            "leads_total": leads_total,
            "active_merchants": active_merchants,
            "messages_received": int(counts.get("message.received", 0)),
            "bookings_created": int(counts.get("booking.created", 0)),
            "reminders_sent": int(counts.get("reminder.sent", 0)),
        }

    async def merchants_ranking(
        self, *, tenant_id: UUID, since_days: int = 30
    ) -> list[MerchantRanking]:
        since = datetime.now(tz=UTC) - timedelta(days=since_days)

        totals_stmt = (
            select(
                Lead.merchant_id.label("merchant_id"),
                func.count(Lead.id).label("leads_total"),
            )
            .join(Merchant, Merchant.id == Lead.merchant_id)
            .where(Merchant.tenant_id == tenant_id)
            .group_by(Lead.merchant_id)
            .subquery()
        )

        bookings_stmt = (
            select(
                AnalyticsEvent.merchant_id.label("merchant_id"),
                func.count(AnalyticsEvent.id).label("bookings"),
            )
            .where(
                AnalyticsEvent.tenant_id == tenant_id,
                AnalyticsEvent.event_type == "booking.created",
                AnalyticsEvent.occurred_at >= since,
            )
            .group_by(AnalyticsEvent.merchant_id)
            .subquery()
        )

        stmt = select(
            totals_stmt.c.merchant_id,
            totals_stmt.c.leads_total,
            func.coalesce(bookings_stmt.c.bookings, 0).label("bookings"),
        ).outerjoin(bookings_stmt, bookings_stmt.c.merchant_id == totals_stmt.c.merchant_id)

        rows = (await self._session.execute(stmt)).all()
        return [
            MerchantRanking(
                merchant_id=merchant_id,
                leads_total=int(leads_total),
                bookings_created=int(bookings),
                conversion_rate=(int(bookings) / int(leads_total)) if leads_total else 0.0,
            )
            for merchant_id, leads_total, bookings in rows
        ]
