"""GHLClient token-refresh persistence test.

GHL rotates the refresh_token on every refresh and invalidates the old one. If
we don't persist the rotated bundle, the next turn reloads a stale refresh_token
from the DB and the integration breaks. This test asserts the `on_token_refresh`
callback fires with the rotated bundle after a 401 → refresh → retry cycle.
"""

from __future__ import annotations

from integrations.ghl.client import GHLClient, GHLTokenBundle


class FakeResp:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = ""
        self.content = b"{}"

    def json(self):
        return self._json


class FakeHttp:
    """Returns queued responses for .request; one fixed response for .post."""

    def __init__(self, request_responses, post_response):
        self._req = list(request_responses)
        self._post = post_response
        self.requests: list[tuple] = []
        self.posts: list[tuple] = []

    async def request(self, method, path, *, json=None, headers=None):
        self.requests.append(
            (method, path, headers.get("Authorization") if headers else None, json)
        )
        return self._req.pop(0)

    async def post(self, path, *, data=None):
        self.posts.append((path, data))
        return self._post

    async def aclose(self):
        return None


async def test_token_refresh_persists_rotated_bundle() -> None:
    persisted: list[GHLTokenBundle] = []

    async def on_refresh(bundle: GHLTokenBundle) -> None:
        persisted.append(bundle)

    http = FakeHttp(
        request_responses=[FakeResp(401), FakeResp(200, {"contact": {"id": "CT-1"}})],
        post_response=FakeResp(
            200,
            {
                "access_token": "NEW_AT",
                "refresh_token": "NEW_RT",
                "expires_in": 3600,
                "locationId": "loc-9",
            },
        ),
    )

    client = GHLClient(
        token_bundle=GHLTokenBundle(
            access_token="OLD_AT", refresh_token="OLD_RT", expires_at=0, location_id="loc-1"
        ),
        client_id="cid",
        client_secret="csec",
        http=http,
        on_token_refresh=on_refresh,
    )

    result = await client.upsert_contact({"phone": "39333000000"})

    assert result == {"contact": {"id": "CT-1"}}
    # Refresh happened: callback fired with the rotated tokens.
    assert len(persisted) == 1
    assert persisted[0].access_token == "NEW_AT"
    assert persisted[0].refresh_token == "NEW_RT"
    assert persisted[0].location_id == "loc-9"
    assert persisted[0].expires_at > 0
    # The retry used the refreshed bearer token.
    assert http.requests[1][2] == "Bearer NEW_AT"


async def test_no_callback_when_no_refresh_needed() -> None:
    persisted: list[GHLTokenBundle] = []

    async def on_refresh(bundle: GHLTokenBundle) -> None:
        persisted.append(bundle)

    http = FakeHttp(
        request_responses=[FakeResp(200, {"contact": {"id": "CT-1"}})],
        post_response=FakeResp(200, {}),
    )
    client = GHLClient(
        token_bundle=GHLTokenBundle(
            access_token="OLD_AT", refresh_token="OLD_RT", expires_at=0, location_id="loc-1"
        ),
        client_id="cid",
        client_secret="csec",
        http=http,
        on_token_refresh=on_refresh,
    )

    await client.upsert_contact({"phone": "39333000000"})

    assert persisted == []
    assert http.posts == []


def _client(http):
    return GHLClient(
        token_bundle=GHLTokenBundle(
            access_token="AT", refresh_token="RT", expires_at=0, location_id="loc-1"
        ),
        client_id="cid",
        client_secret="csec",
        http=http,
    )


async def test_upsert_contact_uses_upsert_endpoint_with_location() -> None:
    http = FakeHttp([FakeResp(200, {"contact": {"id": "CT-1"}})], FakeResp(200, {}))
    await _client(http).upsert_contact({"phone": "39333000000"})

    method, path, _auth, body = http.requests[0]
    assert method == "POST"
    assert path == "/contacts/upsert"  # not the create endpoint /contacts/
    assert body["locationId"] == "loc-1"  # required by GHL, injected from token


async def test_create_booking_includes_location_id() -> None:
    http = FakeHttp([FakeResp(200, {"id": "BK-1"})], FakeResp(200, {}))
    await _client(http).create_booking(
        "CAL-1",
        contact_id="CT-1",
        slot_start_iso="2026-07-15T15:00:00+02:00",
        slot_end_iso="2026-07-15T15:30:00+02:00",
    )

    _method, path, _auth, body = http.requests[0]
    assert path == "/calendars/events/appointments"
    assert body["locationId"] == "loc-1"
    assert body["calendarId"] == "CAL-1"


async def test_get_free_slots_epoch_ms_and_flatten() -> None:
    # Date-keyed availability map, slots are ISO strings.
    resp = FakeResp(
        200,
        {
            "2026-07-15": {"slots": ["2026-07-15T16:00:00+02:00", "2026-07-15T17:00:00+02:00"]},
            "2026-07-16": {"slots": ["2026-07-16T09:00:00+02:00"]},
            "traceId": "abc",
        },
    )
    http = FakeHttp([resp], FakeResp(200, {}))
    slots = await _client(http).get_free_slots(
        "CAL-1", start_iso="2026-07-15T00:00:00+00:00", end_iso="2026-07-18T00:00:00+00:00"
    )

    # Params sent as epoch milliseconds, not ISO.
    path = http.requests[0][1]
    assert "startDate=1784073600000" in path  # 2026-07-15T00:00:00Z in ms
    assert "endDate=" in path
    assert "T00:00:00" not in path  # no ISO leaked into the query
    # Date-keyed response flattened to {"startTime": ISO}.
    assert [s["startTime"] for s in slots] == [
        "2026-07-15T16:00:00+02:00",
        "2026-07-15T17:00:00+02:00",
        "2026-07-16T09:00:00+02:00",
    ]
