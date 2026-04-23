"""Sentry + PostHog bootstrap shared by the API and the worker process.

Both init functions are no-ops when the relevant DSN/key isn't configured so
local `uv run` sessions don't spam upstream services. Call them exactly once
per process (lifespan/startup) — re-calling re-initialises the SDK and
loses in-flight breadcrumbs.
"""

from __future__ import annotations

from shared.settings import Settings


def init_sentry(settings: Settings, *, component: str) -> bool:
    """Initialise sentry-sdk. `component` is emitted as the `service` tag so
    Sentry's issue grouping separates API and worker errors.

    Returns True iff Sentry was activated (DSN present + import succeeded).
    """
    if not settings.sentry_dsn_backend:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn_backend,
        environment=settings.environment,
        # Keep trace sampling low in prod; we turn it up for debugging via
        # env override rather than code changes.
        traces_sample_rate=0.05 if settings.environment == "production" else 1.0,
        profiles_sample_rate=0.0,
        send_default_pii=False,
        integrations=[
            StarletteIntegration(),
            AsyncioIntegration(),
        ],
    )
    sentry_sdk.set_tag("service", component)
    return True


def init_posthog(settings: Settings) -> object | None:
    """Initialise a PostHog client for server-side event emission.

    Returns the client (callers store it on app/ctx) or None when disabled.
    Callers should emit with `posthog.capture(distinct_id, event, properties)`;
    never pass PII as the distinct_id.
    """
    if not settings.posthog_key:
        return None
    try:
        from posthog import Posthog  # type: ignore[import-not-found]
    except ImportError:
        return None
    client: object = Posthog(
        project_api_key=settings.posthog_key,
        host="https://eu.posthog.com",
        # Flush quickly so short-lived request handlers don't drop events.
        flush_at=10,
        flush_interval=2.0,
    )
    return client
