"""Consolidated ARQ WorkerSettings — one process consumes every queue.

Section 5.5: in production we register all handlers under a single
WorkerSettings class to avoid idle Railway instances. The domain split into
conversation/scheduler/fine_tuning stays at the module level for clarity.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.cron import cron

from db import get_engine
from shared import configure_logging, get_settings, init_sentry
from workers.conversation.handlers import (
    handle_ghl_event,
    handle_inbound_message,
    send_outbound_whatsapp,
    update_outbound_status,
)
from workers.fine_tuning.handlers import (
    fine_tune_deploy,
    fine_tune_evaluate,
    fine_tune_train,
)
from workers.runtime import build_runtime
from workers.scheduler.handlers import (
    build_analytics_export,
    daily_kpi_rollup,
    followup_no_answer,
    integration_health_check,
    kb_reindex,
    objection_extraction,
    reactivate_dormant_leads,
)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, environment=settings.environment)
    init_sentry(settings, component="worker")
    get_engine(settings.supabase_db_url)  # initialise session factory
    ctx["settings"] = settings
    ctx["runtime"] = build_runtime(settings)


async def shutdown(ctx: dict[str, Any]) -> None:
    pass


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        # queue: wa:inbound
        handle_inbound_message,
        # queue: wa:outbound (composer-driven human replies)
        send_outbound_whatsapp,
        # queue: wa:status (delivered/read/failed callbacks)
        update_outbound_status,
        # queue: ghl:events
        handle_ghl_event,
        # queue: scheduler:jobs
        followup_no_answer,
        reactivate_dormant_leads,
        daily_kpi_rollup,
        objection_extraction,
        kb_reindex,
        integration_health_check,
        build_analytics_export,
        # queue: ft:pipeline
        fine_tune_train,
        fine_tune_evaluate,
        fine_tune_deploy,
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)

    # In-process ARQ schedules. Times are UTC — Europe/Rome is UTC+1 / UTC+2 (DST),
    # so 09:00 UTC roughly maps to 10:00 CET / 11:00 CEST local.
    cron_jobs: ClassVar[list[Any]] = [
        # UC-03: sweep idle conversations every 15 minutes. Per-merchant thresholds
        # still gate whether a reminder is due, so the tick rate can stay tight.
        cron(followup_no_answer, minute={0, 15, 30, 45}, timeout=300, max_tries=1),
        # UC-06: daily sweep for dormant leads. Send during local business hours.
        cron(reactivate_dormant_leads, hour=9, minute=0, timeout=600, max_tries=1),
        # Daily KPI rollup for yesterday — runs just after UTC midnight.
        cron(daily_kpi_rollup, hour=0, minute=15, timeout=600, max_tries=1),
        # Integration liveness probe every 4 hours — surfaces expired tokens before
        # a real conversation hits them.
        cron(
            integration_health_check,
            hour={0, 4, 8, 12, 16, 20},
            minute=5,
            timeout=300,
            max_tries=1,
        ),
    ]
