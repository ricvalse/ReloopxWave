"""Router — `POST /onboard/start` client.

Server-to-server call. The router mints a one-shot CSRF state token tied
to (platform_id, customer_id, return_url) and returns it. The platform
hands the state to the browser, which navigates to
`https://hub.360dialog.com/dashboard/app/<partner_id>/permissions?redirect_url=
<router>/onboard/callback?platform=<id>&state=<state>`.

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
            raise IntegrationError(
                f"router /onboard/start failed ({resp.status_code})",
                error_code="router_onboard_start_failed",
                status=resp.status_code,
                body=resp.text[:500],
            )
        data = resp.json()
        state = data.get("state")
        if not state:
            raise IntegrationError(
                "router /onboard/start returned no state",
                error_code="router_onboard_start_no_state",
                body=str(data)[:500],
            )
        return OnboardStartResult(
            state=str(state),
            expires_in=int(data.get("expires_in") or 0),
        )
