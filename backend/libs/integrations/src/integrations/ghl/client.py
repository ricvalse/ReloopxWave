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
from typing import Any, cast

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from shared import IntegrationError, get_logger

logger = get_logger(__name__)

GHL_API_BASE = "https://services.leadconnectorhq.com"


def build_contact_custom_fields(
    field_map: dict[str, str], values: dict[str, Any]
) -> list[dict[str, Any]]:
    """Turn collected lead data into GHL `customFields` entries.

    `field_map` is the merchant config `{our_field_name -> GHL custom field id}`;
    `values` is the merged contact/collected data (e.g. from the action payload
    and `turn_ctx.collected_data`). Only mapped fields with a non-empty value
    produce an entry, shaped as `{"id": <field id>, "value": <value>}` — the
    shape GHL's contact upsert expects. Empty map or no matching values → []."""
    out: list[dict[str, Any]] = []
    for our_name, ghl_field_id in field_map.items():
        value = values.get(our_name)
        if value in (None, "", [], {}):
            continue
        if not ghl_field_id:
            continue
        out.append({"id": ghl_field_id, "value": value})
    return out


def extract_location_name(resp: dict[str, Any]) -> str | None:
    """Pull the sub-account display name out of a `GET /locations/{id}` body.

    GHL wraps it as `{"location": {"name": ...}}`; tolerate a flat `{"name": ...}`
    body too. Returns None when no usable name is present. Shared by the INSTALL
    worker and the agency-side "refresh name" endpoint so both parse identically.
    """
    raw_loc = resp.get("location")
    loc = raw_loc if isinstance(raw_loc, dict) else resp
    name = loc.get("name") if isinstance(loc, dict) else None
    return str(name) if name else None


@dataclass(slots=True)
class GHLTokenBundle:
    access_token: str
    refresh_token: str
    expires_at: int  # epoch seconds
    location_id: str | None = None
    company_id: str | None = None
    # "Location" (sub-account token) or "Company" (agency token). GHL requires
    # the refresh grant to carry the matching user_type.
    user_type: str = "Location"


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

    async def upsert_contact(
        self,
        payload: dict[str, Any],
        *,
        custom_fields: list[dict[str, Any]] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        # v2 upsert endpoint: dedupes by phone/email per the location's "Allow
        # Duplicate Contact" setting (avoids creating a new contact on every
        # inbound message). `locationId` is REQUIRED by GHL — inject it from the
        # token bundle when the caller didn't pass one, otherwise the call 400s.
        #
        # `custom_fields` is GHL's `customFields` array — each entry is
        # `{"id": <field id>, "value": ...}` or `{"key": <field key>, "value": ...}`
        # (contractual capitolato sez.5: collected lead data must land on the CRM
        # card). `tags` is GHL's `tags` array. Both are merged with anything the
        # caller already put in `payload` (payload wins on conflict for tags).
        body = dict(payload)
        if self._tokens.location_id and not body.get("locationId"):
            body["locationId"] = self._tokens.location_id
        if custom_fields:
            existing_cf = list(body.get("customFields") or [])
            existing_cf.extend(custom_fields)
            body["customFields"] = existing_cf
        if tags:
            existing_tags = list(body.get("tags") or [])
            # De-dup while preserving order so we don't re-send the same tag.
            merged = existing_tags + [t for t in tags if t not in existing_tags]
            body["tags"] = merged
        return await self._request("POST", "/contacts/upsert", json=body)

    async def get_contact(self, contact_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/contacts/{contact_id}")

    async def add_contact_note(self, contact_id: str, *, body: str) -> dict[str, Any]:
        """Write an internal note on a contact (UC-04: sentiment + collected data
        recorded on the CRM card when the lead advances). `POST
        /contacts/{contactId}/notes`. Best-effort at the call site — a failed
        note must not roll back the pipeline move."""
        return await self._request("POST", f"/contacts/{contact_id}/notes", json={"body": body})

    # ---- Locations ----

    async def get_location(self, location_id: str) -> dict[str, Any]:
        """Fetch a sub-account's details (name, timezone, …) — used to label a
        freshly-installed location in the linking UI."""
        return await self._request("GET", f"/locations/{location_id}")

    # ---- Opportunities / pipelines (UC-04) ----

    async def list_pipelines(self, location_id: str) -> list[dict[str, Any]]:
        resp = await self._request("GET", f"/opportunities/pipelines?locationId={location_id}")
        return cast(list[dict[str, Any]], resp.get("pipelines", []))

    # ---- Calendars (UC-02 — calendar picker + hours sync) ----

    async def list_calendars(self, location_id: str) -> list[dict[str, Any]]:
        """Calendars configured for a location — powers the booking calendar
        picker so the merchant selects which calendar the bot books into."""
        resp = await self._request("GET", f"/calendars/?locationId={location_id}")
        return cast(list[dict[str, Any]], resp.get("calendars", []))

    async def get_calendar(self, calendar_id: str) -> dict[str, Any]:
        """Fetch full calendar metadata including openHours and dateOverrides.

        Used by the two-way hours sync to pull availability settings from GHL.
        `calendars.readonly` scope is already declared in oauth.py.
        """
        return await self._request("GET", f"/calendars/{calendar_id}")

    async def update_calendar_hours(
        self,
        calendar_id: str,
        *,
        open_hours: list[dict[str, Any]],
        date_overrides: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Push openHours + dateOverrides to a GHL calendar.

        Sends only the fields we manage; GHL preserves all other calendar
        settings (name, description, team members, etc.) untouched.
        `calendars.write` scope is already declared in oauth.py.
        """
        return await self._request(
            "PUT",
            f"/calendars/{calendar_id}",
            json={"openHours": open_hours, "dateOverrides": date_overrides},
        )

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
        return cast(list[dict[str, Any]], resp.get("opportunities", []))

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

    async def list_appointments(
        self, calendar_id: str, *, start_iso: str, end_iso: str
    ) -> list[dict[str, Any]]:
        """List calendar appointments in a window (UC-02 reconcile poll).

        GHL `GET /calendars/events` takes the window as epoch-millisecond
        integers (same convention as free-slots) and requires `locationId`.
        The result is normalized to stable snake_case dicts. We tolerate both
        the `events` envelope and a bare list, and both ISO and epoch-ms times,
        so a wire-format surprise degrades gracefully instead of dropping rows.
        """
        start_ms = _iso_to_epoch_ms(start_iso)
        end_ms = _iso_to_epoch_ms(end_iso)
        path = f"/calendars/events?calendarId={calendar_id}&startTime={start_ms}&endTime={end_ms}"
        if self._tokens.location_id:
            path += f"&locationId={self._tokens.location_id}"
        resp = await self._request("GET", path)
        return _normalize_events(resp)

    async def reschedule_appointment(
        self, event_id: str, *, slot_start_iso: str, slot_end_iso: str
    ) -> dict[str, Any]:
        """Move an existing appointment to a new slot (UC-02 reschedule).

        `PUT /calendars/events/appointments/{eventId}` — the eventId is the
        `ghl_appointment_id` we persisted at booking time.
        """
        return await self._request(
            "PUT",
            f"/calendars/events/appointments/{event_id}",
            json={"startTime": slot_start_iso, "endTime": slot_end_iso},
        )

    async def cancel_appointment(self, event_id: str) -> dict[str, Any]:
        """Cancel an appointment (UC-02 cancel).

        `DELETE /calendars/events/{eventId}` — note GHL's delete path drops the
        `/appointments` segment present in create/reschedule.
        """
        return await self._request("DELETE", f"/calendars/events/{event_id}")

    # ---- Token ----

    async def refresh_now(self) -> GHLTokenBundle:
        """Force a token refresh and return the rotated bundle.

        Used to refresh an agency (Company) token outside the lazy 401-retry path
        — e.g. before minting a location token from a near-expired agency token.
        """
        await self._refresh_token()
        return self._tokens

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
                "user_type": self._tokens.user_type,
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
            company_id=data.get("companyId") or data.get("company_id") or self._tokens.company_id,
            user_type=data.get("userType") or self._tokens.user_type,
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


def _event_time_to_iso(value: Any) -> str | None:
    """GHL event times come back as ISO strings; tolerate epoch-ms too."""
    if value is None:
        return None
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC).isoformat()
    return str(value)


def _normalize_events(resp: Any) -> list[dict[str, Any]]:
    """Normalize a GHL calendar-events response to stable snake_case dicts.

    Tolerates the `events` envelope (current GHL shape), an `appointments`
    envelope, or a bare list; skips items without an id. Each item:
    {id, calendar_id, contact_id, title, start_iso, end_iso, status}.
    """
    if isinstance(resp, list):
        raw: list[Any] = resp
    elif isinstance(resp, dict):
        envelope = resp.get("events") or resp.get("appointments") or []
        raw = envelope if isinstance(envelope, list) else []
    else:
        raw = []

    out: list[dict[str, Any]] = []
    for ev in raw:
        if not isinstance(ev, dict):
            continue
        ev_id = ev.get("id") or ev.get("appointmentId") or ev.get("eventId")
        if not ev_id:
            continue
        out.append(
            {
                "id": str(ev_id),
                "calendar_id": ev.get("calendarId"),
                "contact_id": ev.get("contactId"),
                "title": ev.get("title"),
                "start_iso": _event_time_to_iso(ev.get("startTime")),
                "end_iso": _event_time_to_iso(ev.get("endTime")),
                "status": ev.get("appointmentStatus") or ev.get("status"),
            }
        )
    return out
