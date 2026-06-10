"""GoHighLevel REST client.

V1 scope (section 7.1): OAuth2 token refresh, contacts, opportunities + pipeline
move, calendar availability + booking, webhook parsing. Implementation stubs
here; fill in endpoints progressively.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
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
        on_token_refresh: Callable[[GHLTokenBundle], Awaitable[None]] | None = None,
    ) -> None:
        self._tokens = token_bundle
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.AsyncClient(base_url=GHL_API_BASE, timeout=15.0)
        # Invoked with the freshly rotated bundle after a successful refresh so
        # callers can persist it. GHL rotates the refresh_token on every refresh
        # and invalidates the old one — without persisting, the next turn would
        # reload a stale refresh_token from the DB and the integration would
        # break. Best-effort: a persistence failure is logged, not raised.
        self._on_refresh = on_token_refresh

    async def close(self) -> None:
        await self._http.aclose()

    # ---- Contacts ----

    async def upsert_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        # v2 upsert endpoint: dedupes by phone/email per the location's "Allow
        # Duplicate Contact" setting (avoids creating a new contact on every
        # inbound message). `locationId` is REQUIRED by GHL — inject it from the
        # token bundle when the caller didn't pass one, otherwise the call 400s.
        body = dict(payload)
        if self._tokens.location_id and not body.get("locationId"):
            body["locationId"] = self._tokens.location_id
        return await self._request("POST", "/contacts/upsert", json=body)

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

    async def search_opportunities_by_contact(
        self, contact_id: str, *, location_id: str
    ) -> list[dict[str, Any]]:
        """Return opportunities owned by a contact under the given location.

        UC-02 uses this to avoid duplicating opportunities when the bot books
        a follow-up for a lead it has already created an opportunity for.
        """
        resp = await self._request(
            "GET",
            f"/opportunities/search?contact_id={contact_id}&location_id={location_id}",
        )
        return resp.get("opportunities", [])

    async def create_opportunity(
        self,
        *,
        pipeline_id: str,
        stage_id: str,
        contact_id: str,
        location_id: str,
        name: str,
        status: str = "open",
        monetary_value: float | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "pipelineId": pipeline_id,
            "pipelineStageId": stage_id,
            "contactId": contact_id,
            "locationId": location_id,
            "name": name,
            "status": status,
        }
        if monetary_value is not None:
            body["monetaryValue"] = monetary_value
        return await self._request("POST", "/opportunities/", json=body)

    # ---- Calendar (UC-02) ----

    async def get_free_slots(
        self, calendar_id: str, *, start_iso: str, end_iso: str
    ) -> list[dict[str, Any]]:
        # GHL wants the window as epoch-MILLISECOND integers (ISO strings are
        # rejected) and returns an availability map keyed by date
        # (YYYY-MM-DD → {"slots": [ISO, ...]}). Normalize to a flat list of
        # {"startTime": ISO} so callers don't depend on the wire shape.
        start_ms = _iso_to_epoch_ms(start_iso)
        end_ms = _iso_to_epoch_ms(end_iso)
        resp = await self._request(
            "GET",
            f"/calendars/{calendar_id}/free-slots?startDate={start_ms}&endDate={end_ms}",
        )
        return _flatten_slots(resp)

    async def create_booking(
        self,
        calendar_id: str,
        *,
        contact_id: str,
        slot_start_iso: str,
        slot_end_iso: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "calendarId": calendar_id,
            "contactId": contact_id,
            "startTime": slot_start_iso,
            "endTime": slot_end_iso,
        }
        # locationId is required by GHL for appointment creation.
        if self._tokens.location_id:
            body["locationId"] = self._tokens.location_id
        return await self._request("POST", "/calendars/events/appointments", json=body)

    # ---- Internals ----

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=5.0),
        reraise=True,
    )
    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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
        # GHL returns `expires_in` (seconds). Normalize to an absolute epoch,
        # matching the OAuth code-exchange path in oauth.py.
        if "expires_at" in data:
            expires_at = int(data["expires_at"])
        elif "expires_in" in data:
            expires_at = int(time.time()) + int(data["expires_in"])
        else:
            expires_at = 0
        self._tokens = GHLTokenBundle(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", self._tokens.refresh_token),
            expires_at=expires_at,
            location_id=data.get("locationId")
            or data.get("location_id")
            or self._tokens.location_id,
        )
        logger.info("ghl.token_refreshed")
        if self._on_refresh is not None:
            try:
                await self._on_refresh(self._tokens)
            except Exception as e:  # pragma: no cover — persistence is best-effort
                logger.warning("ghl.token_persist_failed", error=str(e))


def _iso_to_epoch_ms(iso: str) -> int:
    """ISO-8601 → epoch milliseconds (GHL free-slots wants ms integers).
    A naïve value is treated as UTC; an offset-aware value is respected."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _flatten_slots(resp: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize GHL's free-slots response to a flat list of {"startTime": ISO}.

    GHL returns an availability map keyed by date:
      {"2026-06-09": {"slots": ["2026-06-09T09:00:00+02:00", ...]}, "traceId": "..."}
    Each slot is usually an ISO string; tolerate object slots too, and fall back
    to a flat {"slots": [...]} shape defensively.
    """
    out: list[dict[str, Any]] = []

    def _push(slot: Any) -> None:
        if isinstance(slot, str):
            out.append({"startTime": slot})
        elif isinstance(slot, dict):
            start = slot.get("startTime") or slot.get("start")
            if start:
                out.append({"startTime": start})

    for value in resp.values():
        if isinstance(value, dict):
            for slot in value.get("slots", []) or []:
                _push(slot)
    if not out and isinstance(resp.get("slots"), list):
        for slot in resp["slots"]:
            _push(slot)
    return out
