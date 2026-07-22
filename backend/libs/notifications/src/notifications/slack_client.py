"""Slack *incoming webhook* client.

Incoming webhooks accept a JSON body (``{"text": ...}`` or ``{"blocks": [...]}``)
and reply with a 200 whose body is the literal string ``ok`` (not JSON). We keep
the same retry split as ``integrations.whatsapp.d360_client``:

- transport errors (connect/read) retry via the tenacity decorator on ``_post``;
- HTTP 429 / 5xx back off honouring ``Retry-After`` in ``post``;
- 4xx (revoked or malformed webhook) fail fast — retrying can't help.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from shared import get_logger

logger = get_logger(__name__)

_MAX_ATTEMPTS = 3
_MAX_RETRY_AFTER_S = 30.0
_DEFAULT_TIMEOUT_S = 10.0


class SlackDeliveryError(Exception):
    """A Slack incoming-webhook POST failed permanently (non-retryable status)."""

    def __init__(self, message: str, *, status: int, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _parse_retry_after(headers: httpx.Headers, *, default: float) -> float:
    """Best-effort ``Retry-After`` seconds. Slack sends an integer count; we
    ignore the (rarer) HTTP-date form and fall back to the caller's default."""
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


class SlackClient:
    """Thin async client for a single Slack incoming webhook URL."""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        # Own the client only when we created it, so an injected shared client
        # (tests / a future pooled client) isn't closed out from under the caller.
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=5.0),
        reraise=True,
        retry=retry_if_exception_type(httpx.TransportError),
    )
    async def _post(self, webhook_url: str, payload: dict[str, Any]) -> httpx.Response:
        """Single POST with transport-level retry only. HTTP *status* handling
        (429/5xx backoff, 4xx fail-fast) lives in ``post``."""
        return await self._http.post(webhook_url, json=payload)

    async def post(self, webhook_url: str, payload: dict[str, Any]) -> None:
        """Deliver ``payload`` to ``webhook_url``. Raises SlackDeliveryError on a
        permanent failure; retries 429/5xx up to ``_MAX_ATTEMPTS``."""
        resp: httpx.Response | None = None
        for attempt in range(_MAX_ATTEMPTS):
            resp = await self._post(webhook_url, payload)
            if resp.status_code < 400:
                return
            retryable = resp.status_code == 429 or resp.status_code >= 500
            if retryable and attempt < _MAX_ATTEMPTS - 1:
                delay = _parse_retry_after(resp.headers, default=0.5 * (2**attempt))
                logger.warning(
                    "slack.post.retry",
                    status=resp.status_code,
                    attempt=attempt + 1,
                    delay_s=round(min(delay, _MAX_RETRY_AFTER_S), 2),
                )
                await asyncio.sleep(min(delay, _MAX_RETRY_AFTER_S))
                continue
            break

        status = resp.status_code if resp is not None else 0
        body = resp.text[:300] if resp is not None else ""
        raise SlackDeliveryError(f"Slack webhook POST failed ({status})", status=status, body=body)
