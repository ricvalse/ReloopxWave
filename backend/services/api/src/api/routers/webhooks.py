"""Public webhooks — no JWT dependency. Signature validation happens per-route.

WhatsApp comes in via the platform-wide router (a 360dialog Partner owned by
relooptech). The router signs every inbound POST with HMAC-SHA256 of the raw
body using the platform's `shared_secret` (`ROUTER_SHARED_SECRET` here). We
verify before parsing — a missing or mismatched `X-Relooptech-Signature` is
401 with the body still on the wire.

Inbound payloads are byte-identical to Meta Cloud API webhooks (360dialog
forwards them unchanged through the router). The same parsers used by the
old direct-360dialog path still apply.

The router treats:
  2xx = done, 5xx = transient retry, 4xx = immediate DLQ.
Stay under the 10s deadline; offload everything heavy to ARQ.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request

from integrations.ghl.signatures import verify_ghl_signature
from integrations.router import SIGNATURE_HEADER, verify_router_signature
from integrations.whatsapp.webhook import (
    parse_inbound_payload,
    parse_message_echo_payload,
    parse_status_payload,
)
from shared import get_logger, get_settings

router = APIRouter()
logger = get_logger(__name__)


@router.post("/whatsapp")
async def whatsapp_inbound(
    request: Request,
    x_relooptech_signature: str = Header(default="", alias=SIGNATURE_HEADER),
) -> dict[str, Any]:
    """Inbound from the router. Body is the unchanged Meta Cloud API envelope
    360dialog forwarded; the channel id lives in
    `entry[].changes[].value.metadata.phone_number_id`. The router has its
    own 24h dedupe, but we still dedup by `messages[].id` / `message_echoes[].id`
    at the arq job-id boundary in case the router ever shards or re-delivers.
    """
    settings = get_settings()
    raw = await request.body()

    if not verify_router_signature(
        raw_body=raw,
        header_value=x_relooptech_signature,
        shared_secret=settings.router_shared_secret,
    ):
        logger.warning(
            "webhook.router.signature_rejected",
            bytes=len(raw),
            has_header=bool(x_relooptech_signature),
        )
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = await request.json()
    except ValueError:
        # 4xx — bad body. The router treats 4xx as immediate DLQ, which is
        # what we want for malformed JSON: re-trying won't fix it.
        raise HTTPException(status_code=400, detail="invalid json") from None

    arq = request.app.state.arq

    # Inbound messages — text-bearing turns go to the orchestrator. The
    # `phone_number_id` on each event comes from the payload itself
    # (`metadata.phone_number_id`); the router does not put it in the URL.
    events = parse_inbound_payload(payload)
    enqueued_msgs = 0
    for ev in events:
        if ev.text is None or not ev.phone_number_id:
            continue
        await arq.enqueue_job(
            "handle_inbound_message",
            ev.phone_number_id,
            ev.from_phone,
            ev.text,
            ev.message_id,
            _job_id=f"wa:msg:{ev.message_id}",  # dedup if the router retries
        )
        enqueued_msgs += 1

    # Coexistence: messages the merchant typed in the WhatsApp Business App
    # on their phone arrive as `smb_message_echoes`. Persist them as outbound
    # `role='agent'` rows so the conversations UI mirrors the phone screen;
    # never run them through the LLM orchestrator (already sent).
    echoes = parse_message_echo_payload(payload)
    enqueued_echoes = 0
    for ev in echoes:
        if ev.text is None or not ev.phone_number_id:
            continue
        if not ev.message_id or not ev.customer_phone:
            continue
        await arq.enqueue_job(
            "handle_phone_app_echo",
            ev.phone_number_id,
            ev.customer_phone,
            ev.text,
            ev.message_id,
            _job_id=f"wa:echo:{ev.message_id}",
        )
        enqueued_echoes += 1

    # Outbound status callbacks (delivered/read/failed/sent). Update the
    # corresponding outbound row's tick state. Dedup on
    # (wa_message_id, status) so retries are idempotent.
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
        "webhook.router.inbound",
        msg_events=len(events),
        msg_enqueued=enqueued_msgs,
        echo_events=len(echoes),
        echo_enqueued=enqueued_echoes,
        status_events=len(statuses),
        status_enqueued=enqueued_statuses,
    )
    return {
        "accepted": len(events) + len(echoes) + len(statuses),
        "enqueued": enqueued_msgs + enqueued_echoes + enqueued_statuses,
    }


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
