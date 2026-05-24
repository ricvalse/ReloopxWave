"""Router — `POST /onboard/start` client.

Server-to-server call. The router mints a one-shot CSRF state token tied
to (platform_id, customer_id, return_url), assembles the full 360dialog
Embedded Signup URL (`connect_url`) using its own copy of the partner_id,
and returns both. The platform navigates the browser straight to
`connect_url` — partner_id never leaves the router.

The router authenticates this call by verifying `X-Relooptech-Signature`
(HMAC-SHA256 of the raw JSON body using the platform's `shared_secret`).
Same scheme as the router→platform direction — see
`integrations.router.signatures` for the primitive.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

import httpx

from integrations.router.signatures import SIGNATURE_HEADER, sign_router_payload
from shared import IntegrationError, get_logger

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class OnboardStartResult:
    connect_url: str
    state: str
    expires_in: int


class RouterClient:
    def __init__(
        self,
        *,
        base_url: str,
        shared_secret: str,
        http: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not base_url:
            raise ValueError("router base_url is required")
        if not base_url.startswith(("http://", "https://")):
            # httpx silently accepts a schemeless base_url at construction
            # and only fails deep inside the connection pool when a request
            # actually fires. Fail loudly here instead — production set
            # `ROUTER_BASE_URL=router.relooptech.ai` once and got a 500
            # with a 20-line httpcore traceback.
            raise ValueError(
                f"router base_url must start with http:// or https:// "
                f"(got: {base_url!r})"
            )
        if not shared_secret:
            raise ValueError("router shared_secret is required")
        self._shared_secret = shared_secret
        self._http = http or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def onboard_start(
        self,
        *,
        platform_id: str,
        customer_id: UUID | str,
        return_url: str,
    ) -> OnboardStartResult:
        """Mint a one-shot state token. Token is consumed when the merchant
        completes 360dialog Embedded Signup and the router's
        `/onboard/callback` fires.
        """
        # Serialize with sorted keys + no whitespace so the bytes we sign
        # exactly match the bytes the router HMAC-verifies. Mirrors the
        # router→platform direction.
        body = json.dumps(
            {
                "platform_id": platform_id,
                "customer_id": str(customer_id),
                "return_url": return_url,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = sign_router_payload(
            raw_body=body, shared_secret=self._shared_secret
        )

        resp = await self._http.post(
            "/onboard/start",
            content=body,
            headers={
                "Content-Type": "application/json",
                SIGNATURE_HEADER: signature,
            },
        )
        if resp.status_code >= 400:
            # Surface the router's rejection reason in our own logs so
            # debugging doesn't require a `railway logs --service router-api`
            # round-trip. 401 here almost always means: secret mismatch
            # between platform's ROUTER_SHARED_SECRET and the router's
            # platform_registry.shared_secret row, or the router didn't
            # rename `X-Amaliatech-Signature` → `X-Relooptech-Signature`,
            # or the router re-serialized JSON before HMAC verification
            # (must verify against raw bytes).
            logger.warning(
                "router.onboard_start.rejected",
                status=resp.status_code,
                body=resp.text[:500],
                request_bytes=len(body),
            )
            raise IntegrationError(
                f"router /onboard/start failed ({resp.status_code})",
                error_code="router_onboard_start_failed",
                status=resp.status_code,
                body=resp.text[:500],
            )
        data = resp.json()
        state = data.get("state")
        connect_url = data.get("connect_url")
        if not state or not connect_url:
            raise IntegrationError(
                "router /onboard/start missing state or connect_url",
                error_code="router_onboard_start_bad_response",
                body=str(data)[:500],
            )
        return OnboardStartResult(
            connect_url=str(connect_url),
            state=str(state),
            expires_in=int(data.get("expires_in") or 0),
        )
