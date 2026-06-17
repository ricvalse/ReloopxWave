"""UC-13 trigger — close idle conversations and enqueue objection extraction.

There is no explicit "conversation closed" event in the WhatsApp flow, so we
approximate it: a conversation with no activity for `IDLE_CLOSE_MINUTES` is
considered finished. On close we enqueue `objection_extraction` for it, which is
the automatic post-conversation extraction the spec calls for (§5.3, §6.5) —
previously the extractor only ran when a human hit the manual API endpoint.

The threshold sits well after the UC-03 follow-up window (default 2nd reminder
at 1440 min) so closing never cuts off a pending reminder sequence.
"""

from __future__ import annotations

from typing import Any

from config_resolver import SYSTEM_DEFAULTS, ConfigKey
from db import ConversationRepository, session_scope
from shared import get_logger

logger = get_logger(__name__)

# Fallback idle threshold (minutes) if the config default is somehow unset.
_IDLE_CLOSE_FALLBACK_MIN = 120


def _idle_close_minutes() -> int:
    """Idle threshold from config (system default for the cascade), not a magic
    constant. The sweep is tenant-agnostic so we read the system-level default;
    a merchant override only changes the per-merchant view, not this sweep."""
    raw = SYSTEM_DEFAULTS.get(ConfigKey.CONVERSATION_IDLE_CLOSE_MINUTES)
    return int(raw) if isinstance(raw, int) else _IDLE_CLOSE_FALLBACK_MIN


async def close_idle_conversations(ctx: dict[str, Any]) -> dict[str, Any]:
    min_idle = _idle_close_minutes()
    async with session_scope() as session:
        repo = ConversationRepository(session)
        closed_ids = await repo.close_idle_active(min_idle_minutes=min_idle)
    # Commit happened on context exit; now fan out extraction jobs.

    redis = ctx.get("redis")
    enqueued = 0
    if redis is not None:
        for cid in closed_ids:
            await redis.enqueue_job(
                "objection_extraction",
                str(cid),
                _job_id=f"obj:extract:{cid}",
            )
            enqueued += 1
    else:  # pragma: no cover — redis is always present in the ARQ worker ctx
        logger.warning("uc13.close_sweep.no_redis")

    logger.info("uc13.close_sweep", closed=len(closed_ids), enqueued=enqueued)
    return {"closed": len(closed_ids), "enqueued": enqueued}
