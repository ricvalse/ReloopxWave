"""SupabaseAdminClient happy-path + error handling.

The router that consumes this is exercised by TEST.md §2 end-to-end; here we
nail down the contract of the HTTP surface so that regressions in headers,
URL shape, or payload are caught without needing a real Supabase project.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from integrations import SupabaseAdminClient
from shared import IntegrationError


def _transport(captured: list[dict[str, Any]], *, response: httpx.Response) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": json.loads(request.content.decode() or "{}"),
            }
        )
        return response

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_invite_sends_correct_request_and_parses_user_id() -> None:
    user_id = str(uuid.uuid4())
    captured: list[dict[str, Any]] = []
    response = httpx.Response(
        200,
        json={"id": user_id, "email": "alice@example.com"},
    )
    http = httpx.AsyncClient(transport=_transport(captured, response=response))
    client = SupabaseAdminClient(
        supabase_url="https://proj.supabase.co",
        service_role_key="sr-123",
        http=http,
    )

    tenant_id = uuid.uuid4()
    merchant_id = uuid.uuid4()
    invited = await client.invite_user_by_email(
        email="Alice@Example.com",
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        role="merchant_admin",
        redirect_to="https://example.com/welcome",
    )

    assert str(invited.id) == user_id
    assert invited.email == "alice@example.com"
    # Two calls: the invite itself (POST /invite) + the follow-up
    # set_app_metadata PUT /admin/users/{id} so claims land in
    # app_metadata (service-role only) rather than raw_user_meta_data
    # (user-writable).
    assert len(captured) == 2

    invite = captured[0]
    assert invite["method"] == "POST"
    assert invite["url"] == "https://proj.supabase.co/auth/v1/admin/invite"
    assert invite["headers"]["apikey"] == "sr-123"
    assert invite["headers"]["authorization"] == "Bearer sr-123"
    assert invite["body"] == {
        "email": "alice@example.com",
        "data": {
            "tenant_id": str(tenant_id),
            "merchant_id": str(merchant_id),
            "role": "merchant_admin",
        },
        "redirect_to": "https://example.com/welcome",
    }

    app_meta = captured[1]
    assert app_meta["method"] == "PUT"
    assert app_meta["url"] == f"https://proj.supabase.co/auth/v1/admin/users/{user_id}"
    assert app_meta["body"] == {
        "app_metadata": {
            "tenant_id": str(tenant_id),
            "merchant_id": str(merchant_id),
            "role": "merchant_admin",
        }
    }


@pytest.mark.asyncio
async def test_invite_accepts_nested_user_shape() -> None:
    """Supabase's older response shape wraps the user under a `user` key.
    The client handles both to avoid breaking when the API version moves.
    """
    user_id = str(uuid.uuid4())
    http = httpx.AsyncClient(
        transport=_transport(
            [],
            response=httpx.Response(200, json={"user": {"id": user_id}}),
        )
    )
    client = SupabaseAdminClient(
        supabase_url="https://proj.supabase.co",
        service_role_key="sr",
        http=http,
    )

    invited = await client.invite_user_by_email(
        email="bob@example.com",
        tenant_id=uuid.uuid4(),
        merchant_id=None,
        role="agency_user",
    )
    assert str(invited.id) == user_id


@pytest.mark.asyncio
async def test_invite_raises_on_http_error_status() -> None:
    http = httpx.AsyncClient(
        transport=_transport(
            [],
            response=httpx.Response(422, json={"code": "email_taken"}),
        )
    )
    client = SupabaseAdminClient(
        supabase_url="https://proj.supabase.co",
        service_role_key="sr",
        http=http,
    )

    with pytest.raises(IntegrationError) as excinfo:
        await client.invite_user_by_email(
            email="taken@example.com",
            tenant_id=uuid.uuid4(),
            merchant_id=None,
            role="agency_admin",
        )
    assert excinfo.value.error_code == "supabase_invite_rejected"


@pytest.mark.asyncio
async def test_invite_raises_when_response_has_no_user_id() -> None:
    http = httpx.AsyncClient(
        transport=_transport([], response=httpx.Response(200, json={"unexpected": True}))
    )
    client = SupabaseAdminClient(
        supabase_url="https://proj.supabase.co",
        service_role_key="sr",
        http=http,
    )

    with pytest.raises(IntegrationError) as excinfo:
        await client.invite_user_by_email(
            email="a@b.c",
            tenant_id=uuid.uuid4(),
            merchant_id=None,
            role="agency_admin",
        )
    assert excinfo.value.error_code == "supabase_invite_missing_id"


@pytest.mark.asyncio
async def test_invite_requires_config() -> None:
    with pytest.raises(IntegrationError) as excinfo:
        SupabaseAdminClient(supabase_url="", service_role_key="sr")
    assert excinfo.value.error_code == "supabase_admin_not_configured"
