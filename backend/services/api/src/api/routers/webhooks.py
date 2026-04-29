"""Public webhooks — no JWT dependency. Signature validation happens per-route.

WhatsApp comes in via 360dialog only — Wave Marketing operates as a single
360dialog Partner; every merchant's number is a channel under that partner.
360dialog forwards Meta's WABA payload shape unchanged, so the parser is the
same one Meta would have used. Inbound trust is path-only: the
`phone_number_id` in the URL is resolved to a merchant by the worker via
the `integrations` table; events for unknown channels are dropped there.
HMAC signature verification is intentionally not enforced (matches the
other Reloop platform).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request

from integrations.ghl.signatures import verify_ghl_signature
from integrations.whatsapp.webhook import parse_inbound_payload, parse_status_payload
from shared import get_logger, get_settings

router = APIRouter()
logger = get_logger(__name__)


async def _handle_whatsapp_inbound(
    request: Request, *, phone_number_id_override: str | None
) -> dict[str, Any]:
    payload = await request.json()
    arq = request.app.state.arq

    # Inbound messages — text-bearing turns go to the orchestrator.
    events = parse_inbound_payload(payload)
    enqueued_msgs = 0
    for ev in events:
        if ev.text is None:
            continue
        # Prefer the URL path segment when present (autonomous-flow channels
        # are pinned to a per-phone webhook); fall back to the channel id
        # carried in `metadata.phone_number_id` so a single shared URL works.
        pnid = phone_number_id_override or ev.phone_number_id
        if not pnid:
            continue
        await arq.enqueue_job(
            "handle_inbound_message",
            pnid,
            ev.from_phone,
            ev.text,
            ev.message_id,
            _job_id=f"wa:msg:{ev.message_id}",  # dedup if 360dialog retries
        )
        enqueued_msgs += 1

    # Outbound status callbacks (delivered/read/failed/sent). Update the
    # corresponding outbound row's tick state. We dedup on
    # (wa_message_id, status) so retried webhooks are idempotent — Meta does
    # NOT promise at-most-once.
    statuses = parse_status_payload(payload)
    enqueued_statuses = 0
    for st in statuses:
        if not st.wa_message_id or not st.status:
            continue
        await arq.enqueue_job(
            "update_outbound_status",
            st.wa_message_id,
            st.status,
            st.timestamp_unix,
            st.raw,
            _job_id=f"wa:status:{st.wa_message_id}:{st.status}",
        )
        enqueued_statuses += 1

    logger.info(
        "webhook.d360.inbound",
        phone_number_id=phone_number_id_override,
        msg_events=len(events),
        msg_enqueued=enqueued_msgs,
        status_events=len(statuses),
        status_enqueued=enqueued_statuses,
    )
    return {
        "accepted": len(events) + len(statuses),
        "enqueued": enqueued_msgs + enqueued_statuses,
    }


@router.post("/whatsapp")
async def whatsapp_inbound_shared(request: Request) -> dict[str, Any]:
    """Channel-id is read from `metadata.phone_number_id` in the body.

    Use this URL when 360dialog is configured with a single Partner-level
    webhook for every channel (the simpler operational pattern). The
    per-phone route below stays in place for the autonomous Embedded
    Signup flow which programmatically pins a per-channel webhook.
    """
    return await _handle_whatsapp_inbound(request, phone_number_id_override=None)


@router.post("/whatsapp/{phone_number_id}")
async def whatsapp_inbound(
    phone_number_id: str,
    request: Request,
) -> dict[str, Any]:
    return await _handle_whatsapp_inbound(
        request, phone_number_id_override=phone_number_id
    )


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
