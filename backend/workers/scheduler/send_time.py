"""S-05 — optimal send-time optimizer.

Weekly cron (Sunday 06:00 UTC). For each active lead with at least 3 inbound
messages, builds an hourly histogram of when the lead has historically replied
and writes the peak hour to leads.optimal_send_hour.

The no_answer scheduler uses optimal_send_hour to delay follow-ups to the
highest-probability reception window.
"""

from __future__ import annotations

from typing import Any

from db import session_scope
from shared import get_logger

logger = get_logger(__name__)

_MIN_MESSAGES = 3  # require at least this many inbound messages to infer a pattern


async def optimize_send_times(ctx: dict[str, Any]) -> dict[str, Any]:
    """Compute optimal_send_hour for all active leads with sufficient history."""
    updated = 0

    async with session_scope() as session:
        from sqlalchemy import text

        # Aggregate inbound message timestamps per lead → hourly histogram
        # Returns (lead_id, hour, count) ordered by lead_id, count DESC
        rows = await session.execute(
            text(
                """
                SELECT
                    c.lead_id,
                    EXTRACT(HOUR FROM m.created_at AT TIME ZONE 'UTC') AS hour,
                    COUNT(*) AS cnt
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE
                    m.direction = 'in'
                    AND m.created_at >= NOW() - INTERVAL '28 days'
                    AND c.lead_id IS NOT NULL
                GROUP BY c.lead_id, hour
                HAVING COUNT(*) >= 1
                """
            )
        )

        # Aggregate in Python: peak hour per lead
        lead_hourly: dict[str, dict[int, int]] = {}
        for row in rows.mappings():
            lid = str(row["lead_id"])
            hour = int(row["hour"])
            cnt = int(row["cnt"])
            lead_hourly.setdefault(lid, {})[hour] = cnt

        for lead_id, histogram in lead_hourly.items():
            total = sum(histogram.values())
            if total < _MIN_MESSAGES:
                continue
            peak_hour = max(histogram, key=histogram.get)  # type: ignore[arg-type]
            await session.execute(
                text("UPDATE leads SET optimal_send_hour = :h WHERE id = :lid"),
                {"h": peak_hour, "lid": lead_id},
            )
            updated += 1

    logger.info("send_time.optimize.done", updated=updated)
    return {"leads_updated": updated}
