from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from shared import get_logger

_rl_logger = get_logger(__name__)


def client_ip(request: Request) -> str:
    """Best-effort client IP, honouring the proxy's X-Forwarded-For first hop."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def rate_limit_key(ip: str, *, now: float) -> str:
    """Fixed 60s window bucket key for an IP."""
    window = int(now // 60)
    return f"rl:public:{ip}:{window}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-IP rate limit on the public, unauthenticated surface.

    Only paths under `prefixes` are limited (webhooks / OAuth callback / internal
    notify) — these run compute before their signature check, so they're the
    DoS-exposed edge. Backed by the shared ARQ Redis pool on `app.state.arq`.
    Fail-open: any Redis hiccup lets the request through rather than 503-ing the
    webhook intake. `limit <= 0` disables it entirely.
    """

    def __init__(self, app, *, limit_per_min: int, prefixes: tuple[str, ...]) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._limit = limit_per_min
        self._prefixes = prefixes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self._limit <= 0 or not request.url.path.startswith(self._prefixes):
            return await call_next(request)

        redis = getattr(request.app.state, "arq", None)
        if redis is None:
            return await call_next(request)

        key = rate_limit_key(client_ip(request), now=time.time())
        try:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, 60)
        except Exception as e:  # fail-open — never let the limiter drop traffic
            _rl_logger.warning("ratelimit.redis_error", error=str(e))
            return await call_next(request)

        if count > self._limit:
            _rl_logger.info("ratelimit.exceeded", path=request.url.path, count=count)
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": "60"},
            )
        return await call_next(request)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a trace_id to every request and binds it to structlog contextvars."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
        )
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["x-trace-id"] = trace_id
        return response
