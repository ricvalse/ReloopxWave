"""360dialog WhatsApp Cloud API client.

360dialog is a BSP that proxies Meta Cloud API. The message payload shape is
identical to Meta's, which means the handler layer can treat the two clients
as interchangeable once we abstract the auth + base-url difference.

Auth: `D360-API-KEY: <api_key>` header (not Bearer).
Base URL: https://waba-v2.360dialog.io
Send endpoint: `/messages` (the API key already scopes to a channel, so
there's no `{phone_number_id}` path component like Meta's).
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

from integrations.whatsapp.ratelimit import acquire_channel_slot, parse_retry_after_seconds
from shared import IntegrationError, get_logger

logger = get_logger(__name__)

D360_BASE = "https://waba-v2.360dialog.io"

# Outbound deliverability guards (see ratelimit.py). Cap per-channel throughput
# and back off on 429/5xx honouring Retry-After, so a burst never trips Meta's
# rate limit and degrades the number's quality rating.
_DEFAULT_MAX_MPS = 8.0  # messages/second per channel
_MAX_SEND_ATTEMPTS = 3
_MAX_RETRY_AFTER_S = 30.0


class D360WhatsAppClient:
    """Drop-in for WhatsAppClient — same method signatures, different wire."""

    def __init__(
        self,
        *,
        api_key: str,
        phone_number_id: str,
        http: httpx.AsyncClient | None = None,
        base_url: str | None = None,
        max_messages_per_second: float = _DEFAULT_MAX_MPS,
    ) -> None:
        self._api_key = api_key
        # phone_number_id is stored alongside the api_key for cross-provider
        # compatibility with the Meta client even though D360 doesn't need it
        # in the request path — handlers index integrations by phone_number_id.
        # It also keys the per-channel outbound rate limiter.
        self._phone_number_id = phone_number_id
        # Minimum spacing between sends on this channel (0 disables throttling).
        self._min_interval_s = 1.0 / max_messages_per_second if max_messages_per_second > 0 else 0.0
        # `base_url` lets the caller honor the per-channel `address` returned
        # by Partner Hub when minting an api_key. Most channels resolve to
        # the same `waba-v2.360dialog.io` host, but 360dialog reserves the
        # right to issue per-region/per-platform URLs — using `address` makes
        # us forward-compatible with that without changing call sites.
        self._base_url = (base_url or D360_BASE).rstrip("/")
        self._http = http or httpx.AsyncClient(base_url=self._base_url, timeout=15.0)

    async def close(self) -> None:
        await self._http.aclose()

    async def send_text(self, *, to_phone: str, text: str) -> dict[str, Any]:
        return await self._send(
            {
                "messaging_product": "whatsapp",
                "to": to_phone,
                "type": "text",
                "text": {"body": text, "preview_url": False},
            }
        )

    async def send_template(
        self,
        *,
        to_phone: str,
        template_name: str,
        language: str = "it",
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return await self._send(
            {
                "messaging_product": "whatsapp",
                "to": to_phone,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": language},
                    "components": components or [],
                },
            }
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=5.0),
        reraise=True,
        retry=retry_if_exception_type(httpx.TransportError),
    )
    async def download_media(self, *, media_id: str) -> tuple[bytes, str] | None:
        """Download inbound media by Meta/360dialog media id. Two-step.

        1. `GET {base_url}/{media_id}` (auth: `D360-API-KEY`) → JSON `{url, mime_type}`.
        2. The `url` points at Meta's CDN (`lookaside.fbsbx.com`), which needs a
           Facebook token 360dialog partners don't hold. Swap the host for the
           360dialog proxy — it injects the token server-side — then GET the
           binary with the same `D360-API-KEY`.

        Returns `(bytes, mime)` or `None` on any failure (best-effort: a media
        failure must never block the text reply). Transport errors retry via the
        decorator; ARQ's job retry is the outer net. 30s per hop — media on a
        slow CDN would be dropped by the client-wide 15s send timeout.
        """
        headers = {"D360-API-KEY": self._api_key}
        try:
            meta_resp = await self._http.get(f"/{media_id}", headers=headers, timeout=30.0)
        except httpx.TransportError:
            raise  # let @retry handle connect/read errors
        except httpx.HTTPError as exc:
            logger.warning("d360.media.metadata_error", media_id=media_id, error=str(exc))
            return None
        if meta_resp.status_code >= 400:
            logger.warning(
                "d360.media.metadata_failed",
                media_id=media_id,
                status=meta_resp.status_code,
            )
            return None
        try:
            meta_json = meta_resp.json()
        except ValueError:
            logger.warning("d360.media.metadata_not_json", media_id=media_id)
            return None
        url = meta_json.get("url")
        if not url:
            logger.warning("d360.media.no_url", media_id=media_id)
            return None

        # Meta CDN URLs require a Facebook token 360dialog partners don't have;
        # rewrite the host to the 360dialog proxy (adds the token server-side).
        # `.replace("\\", "")` strips JSON escaping artefacts (Amalia does the
        # same — `dialog360.py:663-665`).
        proxied = str(url).replace("https://lookaside.fbsbx.com", self._base_url).replace("\\", "")
        try:
            bin_resp = await self._http.get(proxied, headers=headers, timeout=30.0)
        except httpx.TransportError:
            raise
        except httpx.HTTPError as exc:
            logger.warning("d360.media.binary_error", media_id=media_id, error=str(exc))
            return None
        if bin_resp.status_code >= 400:
            logger.warning(
                "d360.media.binary_failed",
                media_id=media_id,
                status=bin_resp.status_code,
            )
            return None
        mime = (
            bin_resp.headers.get("content-type")
            or meta_json.get("mime_type")
            or "application/octet-stream"
        )
        return bin_resp.content, str(mime)

    async def send_typing_indicator(self, *, message_id: str) -> dict[str, Any]:
        """Mark the customer's inbound message as read AND show a "typing…"
        indicator, in one call. This is the WhatsApp Cloud API shape that
        360dialog proxies: POST /messages with `status: read` + a
        `typing_indicator` block. `message_id` is the inbound customer wamid.
        The indicator auto-dismisses after ~25s or when the next message is
        sent, whichever comes first.
        """
        return await self._send(
            {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
                "typing_indicator": {"type": "text"},
            }
        )

    async def send_interactive(
        self,
        *,
        to_phone: str,
        header: str | None,
        body: str,
        buttons: list[dict[str, str]],
    ) -> dict[str, Any]:
        return await self._send(
            {
                "messaging_product": "whatsapp",
                "to": to_phone,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "header": {"type": "text", "text": header} if header else None,
                    "body": {"text": body},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                            for b in buttons
                        ]
                    },
                },
            }
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=5.0),
        reraise=True,
        retry=retry_if_exception_type(httpx.TransportError),
    )
    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        """Single POST with transport-level retry only (connect/read errors).

        HTTP *status* handling (429/5xx backoff, 4xx fail-fast) lives in
        `_send` so we can honour Retry-After and never retry a non-recoverable
        4xx.
        """
        return await self._http.post(
            "/messages",
            json={k: v for k, v in payload.items() if v is not None},
            headers={"D360-API-KEY": self._api_key},
        )

    async def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Per-channel throttle: smooth bursts (multi-bubble, campaigns) so we
        # don't trip Meta's rate limit.
        await acquire_channel_slot(self._phone_number_id, self._min_interval_s)

        resp: httpx.Response | None = None
        for attempt in range(_MAX_SEND_ATTEMPTS):
            resp = await self._post(payload)
            if resp.status_code < 400:
                result: dict[str, Any] = resp.json()
                return result
            # 429 (rate limited) and 5xx (transient) are retryable; honour
            # Retry-After when present, else exponential backoff. 4xx are
            # permanent — fail fast.
            retryable = resp.status_code == 429 or resp.status_code >= 500
            if retryable and attempt < _MAX_SEND_ATTEMPTS - 1:
                delay = parse_retry_after_seconds(resp.headers, default=0.5 * (2**attempt))
                logger.warning(
                    "d360.send.retry",
                    status=resp.status_code,
                    attempt=attempt + 1,
                    delay_s=round(min(delay, _MAX_RETRY_AFTER_S), 2),
                )
                await asyncio.sleep(min(delay, _MAX_RETRY_AFTER_S))
                continue
            break

        status = resp.status_code if resp is not None else 0
        body = resp.text[:500] if resp is not None else ""
        raise IntegrationError(
            f"360dialog send failed ({status})",
            error_code="d360_send_failed",
            status=status,
            body=body,
        )
