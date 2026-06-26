from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.ghl import GhlSyncLog


@dataclass(slots=True, frozen=True)
class GhlSyncEntry:
    id: UUID
    tenant_id: UUID
    merchant_id: UUID | None
    lead_id: UUID | None
    conversation_id: UUID | None
    operation: str
    ghl_entity_type: str | None
    ghl_entity_id: str | None
    status: str
    error_detail: str | None
    payload: dict[str, Any] | None
    result: dict[str, Any] | None
    occurred_at: datetime


class GhlSyncRepository:
    """Append-only log of every GHL API call."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def emit(
        self,
        *,
        tenant_id: UUID,
        merchant_id: UUID | None,
        operation: str,
        ghl_entity_type: str | None = None,
        ghl_entity_id: str | None = None,
        status: str = "success",
        error_detail: str | None = None,
        payload: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        lead_id: UUID | None = None,
        conversation_id: UUID | None = None,
    ) -> GhlSyncLog:
        row = GhlSyncLog(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
            operation=operation,
            ghl_entity_type=ghl_entity_type,
            ghl_entity_id=ghl_entity_id,
            status=status,
            error_detail=error_detail,
            payload=payload,
            result=result,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_for_lead(
        self,
        lead_id: UUID,
        *,
        limit: int = 50,
    ) -> list[GhlSyncEntry]:
        stmt = (
            select(GhlSyncLog)
            .where(GhlSyncLog.lead_id == lead_id)
            .order_by(GhlSyncLog.occurred_at.desc())
            .limit(limit)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_entry(r) for r in rows]

    async def list_for_merchant(
        self,
        merchant_id: UUID,
        *,
        since_days: int = 30,
        limit: int = 100,
    ) -> list[GhlSyncEntry]:
        since = datetime.now(tz=UTC) - timedelta(days=since_days)
        stmt = (
            select(GhlSyncLog)
            .where(GhlSyncLog.merchant_id == merchant_id)
            .where(GhlSyncLog.occurred_at >= since)
            .order_by(GhlSyncLog.occurred_at.desc())
            .limit(limit)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_entry(r) for r in rows]


def _to_entry(r: GhlSyncLog) -> GhlSyncEntry:
    return GhlSyncEntry(
        id=r.id,
        tenant_id=r.tenant_id,
        merchant_id=r.merchant_id,
        lead_id=r.lead_id,
        conversation_id=r.conversation_id,
        operation=r.operation,
        ghl_entity_type=r.ghl_entity_type,
        ghl_entity_id=r.ghl_entity_id,
        status=r.status,
        error_detail=r.error_detail,
        payload=r.payload,
        result=r.result,
        occurred_at=r.occurred_at,
    )
