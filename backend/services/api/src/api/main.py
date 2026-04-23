from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_core import (
    ConversationOrchestrator,
    Embedder,
    ModelRouter,
    PlaygroundRunner,
)
from api.core.errors import register_exception_handlers
from api.core.middleware import RequestContextMiddleware
from api.dependencies.session import init_db
from api.routers import (
    ab_test,
    analytics,
    auth,
    bot_config,
    conversations,
    integrations,
    knowledge_base,
    merchants,
    playground,
    reports,
    tenants,
    users,
    webhooks,
)
from shared import Settings, configure_logging, get_logger, get_settings, init_posthog, init_sentry

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, environment=settings.environment)
    init_sentry(settings, component="api")
    app.state.posthog = init_posthog(settings)
    await init_db(settings.supabase_db_url)

    # ARQ pool for enqueueing from webhook handlers. One shared pool per process.
    app.state.arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))

    # AI wiring for synchronous paths (UC-08 playground).
    router = ModelRouter(settings)
    orchestrator = ConversationOrchestrator(router)
    embedder = Embedder(api_key=settings.openai_api_key) if settings.openai_api_key else None
    app.state.playground = PlaygroundRunner(orchestrator=orchestrator, embedder=embedder)

    try:
        yield
    finally:
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
    register_exception_handlers(app)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    # Authenticated routers (JWT required via dependency inside each file).
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
    app.include_router(merchants.router, prefix="/merchants", tags=["merchants"])
    app.include_router(users.router, prefix="/users", tags=["users"])
    app.include_router(bot_config.router, prefix="/bot-config", tags=["bot-config"])
    app.include_router(knowledge_base.router, prefix="/knowledge-base", tags=["knowledge-base"])
    app.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
    app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
    app.include_router(playground.router, prefix="/playground", tags=["playground"])
    app.include_router(ab_test.router, prefix="/ab-test", tags=["ab-test"])
    app.include_router(reports.router, prefix="/reports", tags=["reports"])
    app.include_router(integrations.router, prefix="/integrations", tags=["integrations"])

    # Public webhooks (signature-validated, no JWT).
    app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])

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
