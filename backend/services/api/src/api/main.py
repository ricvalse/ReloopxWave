from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ai_core import (
    ConversationOrchestrator,
    Embedder,
    FtModelResolver,
    ModelRouter,
    PlaygroundRunner,
    SentimentAnalyzer,
)
from api.core.errors import register_exception_handlers
from api.core.middleware import RateLimitMiddleware, RequestContextMiddleware
from api.dependencies.session import init_db
from api.routers import (
    ab_test,
    analytics,
    appointments,
    auth,
    automations,
    bot_config,
    catalog,
    conversations,
    dsar,
    fine_tuning,
    impersonation,
    integrations,
    internal,
    knowledge_base,
    merchants,
    playground,
    reports,
    tenants,
    users,
    webhooks,
    whatsapp_templates,
)
from config_resolver import set_shared_redis
from shared import Settings, configure_logging, get_logger, get_settings, init_posthog, init_sentry

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, environment=settings.environment)
    # Fail fast in production if a required secret is missing — better a loud
    # boot crash than a half-working service that 500s on the first real request.
    settings.ensure_production_ready()
    if settings.environment == "production":
        for warning in settings.production_config_warnings():
            logger.warning("config.recommended_missing", detail=warning)
    init_sentry(settings, component="api")
    app.state.posthog = init_posthog(settings)
    await init_db(settings.supabase_db_url)

    # ARQ pool for enqueueing from webhook handlers. One shared pool per process.
    app.state.arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))

    # Shared Redis client backing the config-cascade cache. Registered globally
    # so every ConfigResolver (routers, playground) caches + invalidates against
    # the same store the worker uses.
    app.state.redis = Redis.from_url(settings.redis_url)
    set_shared_redis(app.state.redis)

    # AI wiring for synchronous paths (UC-08 playground).
    router = ModelRouter(settings, ft_model_provider=FtModelResolver())
    orchestrator = ConversationOrchestrator(router)
    embedder = (
        Embedder(api_key=settings.openai_api_key, model=settings.llm_model_embedding)
        if settings.openai_api_key
        else None
    )
    app.state.playground = PlaygroundRunner(
        orchestrator=orchestrator,
        embedder=embedder,
        sentiment=SentimentAnalyzer(router),
    )

    try:
        yield
    finally:
        set_shared_redis(None)
        await app.state.redis.aclose()
        await app.state.arq.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="API",
        version="0.1.0",
        lifespan=lifespan,
    )

    origins = _resolve_cors_origins(settings)
    if not origins and settings.environment != "local":
        logger.warning(
            "cors.no_origins_configured",
            hint="set CORS_ALLOWED_ORIGINS or PUBLIC_WEB_ADMIN_URL + PUBLIC_WEB_MERCHANT_URL",
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-trace-id"],
    )
    app.add_middleware(RequestContextMiddleware)

    # Rate-limit the browser-facing public OAuth callback (per-IP is meaningful
    # there). Webhooks and /internal are deliberately NOT limited: they arrive
    # from a single trusted BSP/router IP, so a per-IP cap would throttle every
    # merchant's inbound at once — those paths are guarded by HMAC/signature
    # instead. Fail-open; disabled when rate_limit_public_per_min <= 0.
    app.add_middleware(
        RateLimitMiddleware,
        limit_per_min=settings.rate_limit_public_per_min,
        prefixes=("/integrations/crm/oauth/callback",),
    )

    # Validate the Host header in non-local environments when an allowlist is set
    # (defends against Host-header spoofing / cache poisoning). "*" disables it.
    allowed_hosts = [h.strip() for h in settings.allowed_hosts.split(",") if h.strip()]
    if allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    elif settings.environment != "local":
        logger.warning("trustedhost.no_allowlist", hint="set ALLOWED_HOSTS in production")

    register_exception_handlers(app)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    # Authenticated routers (JWT required via dependency inside each file).
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
    app.include_router(merchants.router, prefix="/merchants", tags=["merchants"])
    app.include_router(users.router, prefix="/users", tags=["users"])
    app.include_router(impersonation.router, prefix="/admin/impersonation", tags=["impersonation"])
    app.include_router(bot_config.router, prefix="/bot-config", tags=["bot-config"])
    app.include_router(knowledge_base.router, prefix="/knowledge-base", tags=["knowledge-base"])
    app.include_router(catalog.router, prefix="/catalog", tags=["catalog"])
    app.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
    app.include_router(appointments.router, prefix="/appointments", tags=["appointments"])
    app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
    app.include_router(playground.router, prefix="/playground", tags=["playground"])
    app.include_router(ab_test.router, prefix="/ab-test", tags=["ab-test"])
    app.include_router(reports.router, prefix="/reports", tags=["reports"])
    app.include_router(fine_tuning.router, prefix="/fine-tuning", tags=["fine-tuning"])
    app.include_router(integrations.router, prefix="/integrations", tags=["integrations"])
    app.include_router(
        whatsapp_templates.router, prefix="/whatsapp-templates", tags=["whatsapp-templates"]
    )
    app.include_router(automations.router, prefix="/automations", tags=["automations"])
    app.include_router(dsar.router, prefix="/dsar", tags=["dsar"])

    # Public webhooks (signature-validated, no JWT).
    app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
    # Router → platform notify endpoints (HMAC-signed, no JWT). The
    # `/internal/whatsapp-connected` path is part of the platform contract
    # in NEWPLATFORM_SETUP.md and must match the router's registered URL.
    app.include_router(internal.router, prefix="/internal", tags=["internal"])

    return app


def _resolve_cors_origins(settings: Settings) -> list[str]:
    """Resolve which browser origins are allowed to call the API.

    Order:
      1. `CORS_ALLOWED_ORIGINS` env (comma-separated) — explicit wins.
      2. `PUBLIC_WEB_ADMIN_URL` + `PUBLIC_WEB_MERCHANT_URL` — the two apps.
      3. Local dev: the Next.js defaults on 3000/3001.
      4. Empty list (warned above) — every cross-origin call will 403.
    """
    if settings.cors_allowed_origins:
        return [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
    if settings.environment == "local":
        return [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
        ]
    urls: list[str] = []
    for url in (settings.public_web_admin_url, settings.public_web_merchant_url):
        if url:
            urls.append(url.rstrip("/"))
    return urls


app = create_app()
