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
from shared import configure_logging, get_settings, init_posthog, init_sentry


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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.environment == "local" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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


app = create_app()
