"""GoHighLevel REST client.

V1 scope (section 7.1): OAuth2 token refresh, contacts, opportunities + pipeline
move, calendar availability + booking, webhook parsing. Implementation stubs
here; fill in endpoints progressively.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from shared import IntegrationError, get_logger

logger = get_logger(__name__)

GHL_API_BASE = "https://services.leadconnectorhq.com"


@dataclass(slots=True)
class GHLTokenBundle:
    access_token: str
    refresh_token: str
    expires_at: int  # epoch seconds
    location_id: str | None = None


class GHLClient:
    def __init__(
        self,
        *,
        token_bundle: GHLTokenBundle,
        client_id: str,
        client_secret: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._tokens = token_bundle
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.AsyncClient(base_url=GHL_API_BASE, timeout=15.0)

    async def close(self) -> None:
        await self._http.aclose()

    # ---- Contacts ----

    async def upsert_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/contacts/", json=payload)

    async def get_contact(self, contact_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/contacts/{contact_id}")

    # ---- Opportunities / pipelines (UC-04) ----

    async def list_pipelines(self, location_id: str) -> list[dict[str, Any]]:
        resp = await self._request("GET", f"/opportunities/pipelines?locationId={location_id}")
        return resp.get("pipelines", [])

    async def move_opportunity(
        self, opportunity_id: str, *, stage_id: str, pipeline_id: str
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            f"/opportunities/{opportunity_id}",
            json={"pipelineId": pipeline_id, "pipelineStageId": stage_id},
        )

    # ---- Calendar (UC-02) ----

    async def get_free_slots(
        self, calendar_id: str, *, start_iso: str, end_iso: str
    ) -> list[dict[str, Any]]:
        resp = await self._request(
            "GET",
            f"/calendars/{calendar_id}/free-slots?startDate={start_iso}&endDate={end_iso}",
        )
        return resp.get("slots", [])

    async def create_booking(
        self,
        calendar_id: str,
        *,
        contact_id: str,
        slot_start_iso: str,
        slot_end_iso: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/calendars/events/appointments",
            json={
                "calendarId": calendar_id,
                "contactId": contact_id,
                "startTime": slot_start_iso,
                "endTime": slot_end_iso,
            },
        )

    # ---- Internals ----

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=5.0),
        reraise=True,
    )
    async def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._tokens.access_token}",
            "Version": "2021-07-28",
            "Accept": "application/json",
        }
        resp = await self._http.request(method, path, json=json, headers=headers)
        if resp.status_code in {401, 403}:
            await self._refresh_token()
            headers["Authorization"] = f"Bearer {self._tokens.access_token}"
            resp = await self._http.request(method, path, json=json, headers=headers)
        if resp.status_code >= 400:
            raise IntegrationError(
                f"GHL {method} {path} failed ({resp.status_code})",
                error_code="ghl_request_failed",
                status=resp.status_code,
                body=resp.text[:500],
            )
        return resp.json() if resp.content else {}

    async def _refresh_token(self) -> None:
        resp = await self._http.post(
            "/oauth/token",
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self._tokens.refresh_token,
            },
        )
        if resp.status_code >= 400:
            raise IntegrationError("GHL token refresh failed", error_code="ghl_refresh_failed")
        data = resp.json()
        self._tokens = GHLTokenBundle(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", self._tokens.refresh_token),
            expires_at=int(data["expires_at"]) if "expires_at" in data else 0,
            location_id=self._tokens.location_id,
        )
        logger.info("ghl.token_refreshed")
