"""Router → platform notify endpoint.

The router calls `POST /internal/whatsapp-connected` on two events:

  - `whatsapp.connected` — fired by `/onboard/callback` after a merchant
    finishes 360dialog Embedded Signup. Body carries the per-channel
    `D360-API-Key` and `waba_base_url` we need for outbound sends. This
    is the only place the router gives us the channel key, so we must
    persist it on receipt.
  - `whatsapp.key_rotated` — fired by `/admin/channels/{pid}/rotate-key`.
    The old key is already invalidated on 360dialog's side; we replace
    our row.

Treat both as upsert on `phone_number_id`. The body is signed the same
way as inbound webhooks (HMAC-SHA256 of the raw bytes,
`X-Relooptech-Signature`). Verify before touching JSON.

The full contract lives in `NEWPLATFORM_SETUP.md` § Phase B.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from db import IntegrationRepository, session_scope
from integrations.router import SIGNATURE_HEADER, verify_router_signature
from shared import get_logger, get_settings

router = APIRouter()
logger = get_logger(__name__)


class NotifyChannel(BaseModel):
    phone_number_id: str = Field(min_length=1, max_length=64)
    waba_id: str | None = Field(default=None, max_length=64)
    phone_number: str | None = Field(default=None, max_length=32)
    channel_id: str | None = Field(default=None, max_length=64)
    channel_api_key: str = Field(min_length=1)
    waba_base_url: str | None = Field(default=None, max_length=256)


class NotifyPayload(BaseModel):
    event: str
    platform_id: str
    customer_id: str
    channels: list[NotifyChannel]


# NB: do NOT take the tenant-scoped `DBSession` dependency here. It resolves
# through `get_tenant_context` → `verify_supabase_jwt`, which FastAPI runs
# *before* this handler body — so a router notify (signed, no JWT) would be
# rejected with 403 `missing_token` before the signature check below ever runs.
# The router authenticates by HMAC signature, not a Supabase JWT. We open a
# plain `session_scope()` after verifying the signature, same as the inbound
# webhook → worker path does for other externally-triggered writes.
@router.post("/whatsapp-connected")
async def whatsapp_connected(
    request: Request,
    x_relooptech_signature: str = Header(default="", alias=SIGNATURE_HEADER),
) -> dict[str, Any]:
    settings = get_settings()
    raw = await request.body()

    if not verify_router_signature(
        raw_body=raw,
        header_value=x_relooptech_signature,
        shared_secret=settings.router_shared_secret,
    ):
        logger.warning(
            "router.notify.signature_rejected",
            bytes=len(raw),
            has_header=bool(x_relooptech_signature),
        )
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = NotifyPayload.model_validate_json(raw)
    except ValidationError as exc:
        # 4xx — router will DLQ the call. Bug on our side: re-emit by ops via
        # `/admin/channels/<pid>/rotate-key` once we ship the fix.
        logger.warning("router.notify.invalid_payload", errors=exc.errors())
        raise HTTPException(status_code=400, detail="invalid payload") from exc

    if payload.event not in {"whatsapp.connected", "whatsapp.key_rotated"}:
        # NB: structlog's first positional arg is the log event name, so the
        # payload's `event` field must be passed under a different key.
        logger.warning("router.notify.unknown_event", notify_event=payload.event)
        raise HTTPException(status_code=400, detail="unknown event")

    if payload.platform_id != settings.router_platform_id:
        # Misrouted call — somebody pointed a different platform's router at us.
        # 401 not 404: we don't want to leak which platform_ids are valid.
        logger.warning(
            "router.notify.platform_mismatch",
            received=payload.platform_id,
            expected=settings.router_platform_id,
        )
        raise HTTPException(status_code=401, detail="platform mismatch")

    try:
        merchant_id = UUID(payload.customer_id)
    except ValueError as exc:
        logger.warning(
            "router.notify.bad_customer_id", customer_id=payload.customer_id
        )
        raise HTTPException(status_code=400, detail="invalid customer_id") from exc

    async with session_scope() as session:
        repo = IntegrationRepository(
            session, kek_base64=settings.integrations_kek_base64
        )
        for ch in payload.channels:
            await repo.upsert_whatsapp(
                merchant_id=merchant_id,
                phone_number_id=ch.phone_number_id,
                api_key=ch.channel_api_key,
                channel_id=ch.channel_id,
                waba_id=ch.waba_id,
                waba_base_url=ch.waba_base_url,
                display_phone=ch.phone_number,
            )
            logger.info(
                "router.notify.channel_persisted",
                notify_event=payload.event,
                merchant_id=str(merchant_id),
                phone_number_id=ch.phone_number_id,
                channel_id=ch.channel_id,
                waba_id=ch.waba_id,
                rotated=payload.event == "whatsapp.key_rotated",
            )

    return {"received": len(payload.channels), "event": payload.event}
