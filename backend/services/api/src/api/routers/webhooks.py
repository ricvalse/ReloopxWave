"""Public webhooks — no JWT dependency. Signature validation happens per-route.

The contract from section 7.3:
- respond 200 synchronously (Meta/GHL have short timeouts)
- enqueue the event on the right ARQ queue and return immediately
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response

from integrations.ghl.signatures import verify_ghl_signature
from integrations.whatsapp.webhook import parse_inbound_payload, verify_whatsapp_signature
from shared import get_logger, get_settings

router = APIRouter()
logger = get_logger(__name__)


@router.get("/whatsapp/{phone_number_id}")
async def whatsapp_verify(
    phone_number_id: str,
    hub_mode: str = "",
    hub_challenge: str = "",
    hub_verify_token: str = "",
) -> Response:
    """Meta verification challenge — echo hub.challenge iff verify_token matches."""
    settings = get_settings()
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="verify token mismatch")


@router.post("/whatsapp/{phone_number_id}")
async def whatsapp_inbound(
    phone_number_id: str,
    request: Request,
    x_hub_signature_256: str = Header(default=""),
) -> dict[str, Any]:
    settings = get_settings()
    body = await request.body()
    if not verify_whatsapp_signature(
        app_secret=settings.whatsapp_app_secret,
        payload=body,
        signature_header=x_hub_signature_256,
    ):
        raise HTTPException(status_code=401, detail="invalid signature")

    payload = await request.json()
    events = parse_inbound_payload(payload)
    logger.info(
        "webhook.whatsapp.inbound",
        phone_number_id=phone_number_id,
        events=len(events),
    )

    arq = request.app.state.arq
    enqueued = 0
    for ev in events:
        # Only text-bearing turns are handed off to the orchestrator for now. Status
        # callbacks and media-only messages are ignored here — UC-07 will extend this.
        if ev.text is None:
            continue
        await arq.enqueue_job(
            "handle_inbound_message",
            phone_number_id,
            ev.from_phone,
            ev.text,
            ev.message_id,
            _job_id=f"wa:msg:{ev.message_id}",  # dedup if Meta retries
        )
        enqueued += 1
    return {"accepted": len(events), "enqueued": enqueued}


@router.post("/ghl/{merchant_id}")
async def ghl_inbound(
    merchant_id: UUID,
    request: Request,
    x_gohighlevel_signature: str = Header(default=""),
) -> dict[str, Any]:
    settings = get_settings()
    body = await request.body()
    if not verify_ghl_signature(
        shared_secret=settings.ghl_webhook_secret,
        payload=body,
        signature_header=x_gohighlevel_signature,
    ):
        logger.warning(
            "webhook.ghl.signature_rejected",
            merchant_id=str(merchant_id),
            bytes=len(body),
        )
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = await request.json()
    except ValueError:
        payload = {}

    event_type = str(payload.get("type") or payload.get("event") or "unknown")
    arq = request.app.state.arq
    await arq.enqueue_job(
        "handle_ghl_event",
        str(merchant_id),
        event_type,
        payload,
        _queue_name="ghl:events",
    )
    logger.info(
        "webhook.ghl.inbound.enqueued",
        merchant_id=str(merchant_id),
        event_type=event_type,
    )
    return {"accepted": True, "event_type": event_type}
