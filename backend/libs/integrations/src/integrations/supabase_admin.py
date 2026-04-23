"""Thin wrapper over the Supabase Auth admin REST API.

Used exclusively to invite users: every call goes through the project's
`service_role` key, so callers must already have enforced an admin role and
logged the invocation with an `actor_id`. Keep the surface minimal — this
module is the only legitimate home for service_role HTTP calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from shared import IntegrationError, get_logger

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class InvitedUser:
    id: UUID
    email: str


class SupabaseAdminClient:
    """Async HTTP client bound to a Supabase project's admin endpoints.

    The `data` payload below is written to `raw_user_meta_data`. For V1 the JWT
    hook is expected to promote the three claim keys (`tenant_id`,
    `merchant_id`, `role`) regardless of where they are stored. Moving them to
    `app_metadata` (which users cannot edit) is tracked as a follow-up.
    """

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if not supabase_url or not service_role_key:
            raise IntegrationError(
                "SupabaseAdminClient requires supabase_url + service_role_key",
                error_code="supabase_admin_not_configured",
            )
        self._base = supabase_url.rstrip("/")
        self._key = service_role_key
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._owns_http = http is None

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def invite_user_by_email(
        self,
        *,
        email: str,
        tenant_id: UUID,
        merchant_id: UUID | None,
        role: str,
        redirect_to: str | None = None,
    ) -> InvitedUser:
        payload: dict[str, Any] = {
            "email": email.lower(),
            "data": {
                "tenant_id": str(tenant_id),
                "merchant_id": str(merchant_id) if merchant_id else None,
                "role": role,
            },
        }
        if redirect_to:
            payload["redirect_to"] = redirect_to

        url = f"{self._base}/auth/v1/admin/invite"
        try:
            resp = await self._http.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as e:
            raise IntegrationError(
                "Supabase invite transport failure",
                error_code="supabase_invite_transport",
                reason=str(e),
            ) from e

        if resp.status_code >= 400:
            raise IntegrationError(
                "Supabase rejected invite",
                error_code="supabase_invite_rejected",
                status_code=resp.status_code,
                body=resp.text[:500],
            )

        body = resp.json()
        user_id = body.get("id") or body.get("user", {}).get("id")
        if not user_id:
            raise IntegrationError(
                "Supabase invite returned no user id",
                error_code="supabase_invite_missing_id",
                body=body,
            )
        return InvitedUser(id=UUID(str(user_id)), email=email.lower())

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
