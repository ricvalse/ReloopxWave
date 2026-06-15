"""GHL agency-token exchange + location-token minting.

Covers the two marketplace-specific OAuth calls: exchanging the code with
`user_type="Company"` (agency token, carries companyId) and minting a
Location-level token from the agency token via `/oauth/locationToken`.
"""

from __future__ import annotations

import pytest

from integrations.ghl.oauth import exchange_authorization_code, mint_location_token
from shared import IntegrationError


class FakeResp:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self) -> dict:
        return self._json


class FakePostHttp:
    def __init__(self, response: FakeResp):
        self._resp = response
        self.calls: list[tuple] = []

    async def post(self, url, *, data=None, headers=None):
        self.calls.append((url, data, headers))
        return self._resp

    async def aclose(self) -> None:
        return None


async def test_exchange_company_parses_company_id() -> None:
    http = FakePostHttp(
        FakeResp(
            200,
            {
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 86400,
                "userType": "Company",
                "companyId": "comp-1",
            },
        )
    )
    tokens = await exchange_authorization_code(
        code="c",
        client_id="cid",
        client_secret="cs",
        redirect_uri="https://cb",
        user_type="Company",
        http=http,
    )
    assert tokens.company_id == "comp-1"
    assert tokens.user_type == "Company"
    assert tokens.location_id is None
    assert tokens.expires_at > 0
    _url, data, _headers = http.calls[0]
    assert data["user_type"] == "Company"


async def test_mint_location_token_builds_body_and_headers() -> None:
    http = FakePostHttp(
        FakeResp(
            200,
            {
                "access_token": "LAT",
                "refresh_token": "LRT",
                "expires_in": 86400,
                "locationId": "loc-9",
                "companyId": "comp-1",
            },
        )
    )
    minted = await mint_location_token(
        agency_access_token="AGENCY_AT",
        company_id="comp-1",
        location_id="loc-9",
        http=http,
    )
    assert minted.access_token == "LAT"
    assert minted.refresh_token == "LRT"
    assert minted.location_id == "loc-9"
    assert minted.company_id == "comp-1"
    assert minted.expires_at > 0

    url, data, headers = http.calls[0]
    assert url.endswith("/oauth/locationToken")
    assert data == {"companyId": "comp-1", "locationId": "loc-9"}
    assert headers["Authorization"] == "Bearer AGENCY_AT"


async def test_mint_location_token_rejected_raises() -> None:
    http = FakePostHttp(FakeResp(401, text="unauthorized"))
    with pytest.raises(IntegrationError) as excinfo:
        await mint_location_token(
            agency_access_token="STALE",
            company_id="comp-1",
            location_id="loc-9",
            http=http,
        )
    assert excinfo.value.error_code == "ghl_location_mint_rejected"
