"""Consolidated ARQ WorkerSettings — one process consumes every queue.

Section 5.5: in production we register all handlers under a single
WorkerSettings class to avoid idle Railway instances. The domain split into
conversation/scheduler/fine_tuning stays at the module level for clarity.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.cron import cron
from redis.asyncio import Redis

from config_resolver import set_shared_redis
from db import get_engine
from shared import configure_logging, get_settings, init_sentry
from workers.automation.engine import automation_dispatch, automation_run
from workers.conversation.handlers import (
    flush_inbound_reply,
    handle_ghl_event,
    handle_ghl_install,
    handle_ghl_uninstall,
    handle_inbound_message,
    handle_phone_app_echo,
    send_outbound_whatsapp,
    update_outbound_status,
)
from workers.fine_tuning.handlers import (
    fine_tune_deploy,
    fine_tune_evaluate,
    fine_tune_run,
    fine_tune_train,
)
from workers.runtime import build_runtime
from workers.scheduler.handlers import (
    apply_template_status_event,
    build_analytics_export,
    catalog_reindex,
    close_idle_conversations,
    daily_kpi_rollup,
    enforce_retention,
    followup_no_answer,
    integration_health_check,
    kb_reindex,
    objection_extraction,
    reactivate_dormant_leads,
    send_appointment_reminders,
    sync_appointments,
    template_status_sync,
)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, environment=settings.environment)
    settings.ensure_production_ready()  # fail fast on missing prod secrets
    init_sentry(settings, component="worker")
    get_engine(settings.supabase_db_url)  # initialise session factory
    # Shared Redis for the config-cascade cache (same store the API invalidates
    # against on config writes). Best-effort: a Redis blip degrades to DB reads.
    ctx["config_redis"] = Redis.from_url(settings.redis_url)
    set_shared_redis(ctx["config_redis"])
    ctx["settings"] = settings
    ctx["runtime"] = build_runtime(settings)


async def shutdown(ctx: dict[str, Any]) -> None:
    set_shared_redis(None)
    redis = ctx.get("config_redis")
    if redis is not None:
        await redis.aclose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        # queue: wa:inbound
        handle_inbound_message,
        # debounce flush — coalesces rapid inbound messages into one reply
        flush_inbound_reply,
        # queue: wa:echo (360dialog Coexistence — messages typed in the phone app)
        handle_phone_app_echo,
        # queue: wa:outbound (composer-driven human replies)
        send_outbound_whatsapp,
        # queue: wa:status (delivered/read/failed callbacks)
        update_outbound_status,
        # queue: ghl:events
        handle_ghl_event,
        handle_ghl_install,
        handle_ghl_uninstall,
        # queue: scheduler:jobs
        followup_no_answer,
        reactivate_dormant_leads,
        daily_kpi_rollup,
        objection_extraction,
        close_idle_conversations,
        kb_reindex,
        catalog_reindex,
        integration_health_check,
        build_analytics_export,
        enforce_retention,
        # queue: scheduler:jobs — UC-02 appointment reconcile poll + reminders
        sync_appointments,
        send_appointment_reminders,
        # WhatsApp template approval-status sync (webhook-driven + cron fallback)
        apply_template_status_event,
        template_status_sync,
        # Automazioni — visual flow builder dispatch + run
        automation_dispatch,
        automation_run,
        # queue: ft:pipeline
        fine_tune_run,
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
        # UC-13: hourly sweep that closes long-idle conversations and enqueues
        # objection extraction for each — the automatic post-conversation
        # trigger the spec calls for (previously extraction was manual-only).
        cron(close_idle_conversations, minute=20, timeout=300, max_tries=1),
        # GDPR retention: nightly purge of conversation data past each merchant's
        # privacy.retention_months window. 03:30 UTC — off-peak.
        cron(enforce_retention, hour=3, minute=30, timeout=600, max_tries=1),
        # WhatsApp template approval sync — hourly fallback for any
        # message_template_status_update webhook we missed. Webhook is primary.
        cron(template_status_sync, minute=40, timeout=300, max_tries=1),
        # UC-02: reconcile the local appointment mirror with GHL every 30 min.
        # GHL sends no appointment webhooks, so this poll is the only way manual
        # GHL-side reschedules/cancels/new bookings reach the mirror.
        cron(sync_appointments, minute={10, 40}, timeout=300, max_tries=1),
        # UC-02: send appointment reminders ahead of the slot. Every 30 min,
        # offset from the reconcile poll; per-appointment dedup via meta marker.
        cron(send_appointment_reminders, minute={5, 35}, timeout=300, max_tries=1),
        # Automazioni: tail analytics_events every minute and fan out flow runs.
        cron(automation_dispatch, minute=set(range(60)), timeout=120, max_tries=1),
    ]
