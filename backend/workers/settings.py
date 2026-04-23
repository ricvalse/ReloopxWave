"""Consolidated ARQ WorkerSettings — one process consumes every queue.

Section 5.5: in production we register all handlers under a single
WorkerSettings class to avoid idle Railway instances. The domain split into
conversation/scheduler/fine_tuning stays at the module level for clarity.
"""

from __future__ import annotations

from arq.connections import RedisSettings

from db import get_engine
from shared import configure_logging, get_settings
from workers.conversation.handlers import handle_ghl_event, handle_inbound_message
from workers.fine_tuning.handlers import (
    fine_tune_deploy,
    fine_tune_evaluate,
    fine_tune_train,
)
from workers.runtime import build_runtime
from workers.scheduler.handlers import (
    daily_kpi_rollup,
    followup_no_answer,
    kb_reindex,
    objection_extraction,
    reactivate_dormant_leads,
)


async def startup(ctx: dict) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, environment=settings.environment)
    get_engine(settings.supabase_db_url)  # initialise session factory
    ctx["settings"] = settings
    ctx["runtime"] = build_runtime(settings)


async def shutdown(ctx: dict) -> None:
    pass


class WorkerSettings:
    functions = [
        # queue: wa:inbound
        handle_inbound_message,
        # queue: ghl:events
        handle_ghl_event,
        # queue: scheduler:jobs
        followup_no_answer,
        reactivate_dormant_leads,
        daily_kpi_rollup,
        objection_extraction,
        kb_reindex,
        # queue: ft:pipeline
        fine_tune_train,
        fine_tune_evaluate,
        fine_tune_deploy,
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    # Cron jobs configured via Railway Cron, not in-process, so this stays empty.
    cron_jobs: list = []
