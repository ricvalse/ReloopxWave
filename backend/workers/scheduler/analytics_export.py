"""Analytics CSV export — materializes a tenant's recent analytics_events
into a single CSV and uploads it to Supabase Storage.

The file lives at `<exports_bucket>/<tenant_id>/<export_id>.csv`. The API
hands back a signed URL for it via GET /analytics/exports/{export_id}.
There's no DB state for export jobs in V1 — the storage object's
existence is the state, and Supabase returns 404 if the worker hasn't
finished yet.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import session_scope
from db.models import AnalyticsEvent
from integrations import SupabaseStorage
from shared import get_logger, get_settings

logger = get_logger(__name__)


async def build_analytics_export(
    ctx: dict[str, Any],
    tenant_id: str,
    export_id: str,
    *,
    since_days: int = 30,
) -> dict[str, Any]:
    """Pull `analytics_events` for the tenant in the window, flatten to CSV,
    upload. Returns the bucket-relative path so the caller's logs make sense.
    """
    settings = get_settings()
    tenant_uuid = UUID(tenant_id)

    async with session_scope() as session:
        events = await _load_events(session, tenant_id=tenant_uuid, since_days=since_days)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "occurred_at",
            "event_type",
            "merchant_id",
            "subject_type",
            "subject_id",
            "variant_id",
            "properties_json",
        ]
    )
    for ev in events:
        writer.writerow(
            [
                ev.occurred_at.isoformat() if ev.occurred_at else "",
                ev.event_type,
                str(ev.merchant_id) if ev.merchant_id else "",
                ev.subject_type or "",
                str(ev.subject_id) if ev.subject_id else "",
                ev.variant_id or "",
                _json_compact(ev.properties or {}),
            ]
        )
    body = buffer.getvalue().encode("utf-8")

    path = f"{tenant_id}/{export_id}.csv"
    storage = SupabaseStorage(
        project_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
        bucket=settings.supabase_exports_bucket,
    )
    await storage.upload_bytes(path, body, content_type="text/csv; charset=utf-8")

    logger.info(
        "analytics.export.uploaded",
        tenant_id=tenant_id,
        export_id=export_id,
        events_written=len(events),
        path=path,
        generated_at=datetime.now(tz=UTC).isoformat(),
    )
    return {
        "tenant_id": tenant_id,
        "export_id": export_id,
        "path": path,
        "events_written": len(events),
    }


async def _load_events(
    session: AsyncSession, *, tenant_id: UUID, since_days: int
) -> list[AnalyticsEvent]:
    cutoff = datetime.now(tz=UTC) - timedelta(days=since_days)
    stmt = (
        select(AnalyticsEvent)
        .where(AnalyticsEvent.tenant_id == tenant_id)
        .where(AnalyticsEvent.occurred_at >= cutoff)
        .order_by(AnalyticsEvent.occurred_at.asc())
    )
    return list((await session.execute(stmt)).scalars())


def _json_compact(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
