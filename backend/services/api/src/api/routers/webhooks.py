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

from fastapi import APIRouter, Header, HTTPException, Request

from integrations.ghl.marketplace_signatures import verify_ghl_marketplace_webhook
from integrations.router import SIGNATURE_HEADER, verify_router_signature
from integrations.whatsapp.webhook import (
    parse_inbound_payload,
    parse_message_echo_payload,
    parse_status_payload,
    parse_template_status_payload,
)
from shared import get_logger, get_settings

router = APIRouter()
logger = get_logger(__name__)

# Non-text inbound: instead of dropping it silently (the customer would get no
# reply at all), synthesize a short placeholder so the bot can respond
# gracefully ("ricevo solo messaggi di testo, puoi descrivermi…"). V1 does not
# transcribe/OCR media — this just avoids a dead end.
_MEDIA_PLACEHOLDER = {
    "image": "[Il cliente ha inviato un'immagine]",
    "audio": "[Il cliente ha inviato un messaggio vocale]",
    "video": "[Il cliente ha inviato un video]",
    "document": "[Il cliente ha inviato un documento]",
    "location": "[Il cliente ha condiviso una posizione]",
    "sticker": "[Il cliente ha inviato uno sticker]",
    "contacts": "[Il cliente ha inviato un contatto]",
}

# Media the bot can't meaningfully act on → hand straight to a human (Amalia
# pattern) instead of replying with a placeholder. Lighter media (image/audio/
# location/…) still get a graceful bot reply via `_MEDIA_PLACEHOLDER`.
_HANDOFF_MEDIA = {"video", "document"}

_CAMPAIGN_MAX_LEN = 200


def _extract_campaign(raw: dict[str, Any]) -> str | None:
    """Campaign attribution from a click-to-WhatsApp ad (UC-11).

    WhatsApp puts an ad's metadata on the inbound message's `referral` object.
    Prefer the stable `source_id` (the ad/campaign id); fall back to the
    human-readable `headline`. None for organic messages (no `referral`).
    """
    referral = raw.get("referral")
    if not isinstance(referral, dict):
        return None
    value = referral.get("source_id") or referral.get("headline")
    if not value:
        return None
    return str(value)[:_CAMPAIGN_MAX_LEN]


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
        if not ev.phone_number_id or not ev.message_id:
            continue
        # Text-bearing turns go straight to the orchestrator; media turns get a
        # synthesized placeholder so the customer still gets a graceful reply.
        text = ev.text if ev.text is not None else _MEDIA_PLACEHOLDER.get(ev.kind)
        if text is None:
            continue  # unknown/empty event with no text and no known media kind
        # Rich media (video/document) the bot can't act on → hand off to a human.
        handoff_reason = f"{ev.kind}_message" if ev.kind in _HANDOFF_MEDIA else None
        await arq.enqueue_job(
            "handle_inbound_message",
            ev.phone_number_id,
            ev.from_phone,
            text,
            ev.message_id,
            _extract_campaign(ev.raw),  # UC-11 click-to-WhatsApp ad attribution
            handoff_reason,
            ev.timestamp_unix,  # inbound-staleness gate (None = no check)
            _job_id=f"wa:msg:{ev.message_id}",  # dedup if the router retries
        )
        enqueued_msgs += 1

    # Coexistence: messages the merchant typed in the WhatsApp Business App
    # on their phone arrive as `smb_message_echoes`. Persist them as outbound
    # `role='agent'` rows so the conversations UI mirrors the phone screen;
    # never run them through the LLM orchestrator (already sent).
    echoes = parse_message_echo_payload(payload)
    enqueued_echoes = 0
    for echo in echoes:
        if echo.text is None or not echo.phone_number_id:
            continue
        if not echo.message_id or not echo.customer_phone:
            continue
        await arq.enqueue_job(
            "handle_phone_app_echo",
            echo.phone_number_id,
            echo.customer_phone,
            echo.text,
            echo.message_id,
            _job_id=f"wa:echo:{echo.message_id}",
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

    # Template approval-status updates (message_template_status_update). Apply
    # the local status flip in the worker so the webhook stays fast.
    template_events = parse_template_status_payload(payload)
    enqueued_templates = 0
    for tev in template_events:
        if not tev.template_name or not tev.event:
            continue
        await arq.enqueue_job(
            "apply_template_status_event",
            tev.template_name,
            tev.event,
            tev.reason,
            tev.template_id,
            _job_id=f"wa:tplstatus:{tev.template_name}:{tev.event}",
        )
        enqueued_templates += 1

    logger.info(
        "webhook.router.inbound",
        msg_events=len(events),
        msg_enqueued=enqueued_msgs,
        echo_events=len(echoes),
        echo_enqueued=enqueued_echoes,
        status_events=len(statuses),
        status_enqueued=enqueued_statuses,
        template_events=len(template_events),
        template_enqueued=enqueued_templates,
    )
    return {
        "accepted": len(events) + len(echoes) + len(statuses) + len(template_events),
        "enqueued": (enqueued_msgs + enqueued_echoes + enqueued_statuses + enqueued_templates),
    }


def _ghl_event_dedup_key(payload: dict[str, Any], location_id: Any, raw_type: str) -> str:
    """Stable dedup key for a GHL data webhook (#26).

    GHL retries a delivery until it gets a 2xx, so the same event can arrive
    more than once. Prefer GHL's own event id when present; otherwise fall back
    to a composite of contactId + type + timestamp so distinct events stay
    distinct while a true re-delivery collapses to the same key.
    """
    event_id = (
        payload.get("webhookId")
        or payload.get("eventId")
        or payload.get("id")
        or payload.get("messageId")
    )
    if event_id:
        return str(event_id)
    contact_id = payload.get("contactId") or payload.get("contact_id") or "nocontact"
    timestamp = (
        payload.get("timestamp") or payload.get("dateAdded") or payload.get("dateUpdated") or ""
    )
    return f"{location_id}:{contact_id}:{raw_type}:{timestamp}"


@router.post("/ghl/marketplace")
async def ghl_marketplace_webhook(
    request: Request,
    x_ghl_signature: str = Header(default=""),
    x_wh_signature: str = Header(default=""),
) -> dict[str, Any]:
    """GHL marketplace lifecycle events (INSTALL / UNINSTALL).

    Sent to the app's Default Webhook URL and signed by GHL with Ed25519
    (`x-ghl-signature`, current) or, on the legacy path, RSA-SHA256
    (`x-wh-signature`, deprecated 2026-07-01) — not the HMAC used for
    per-location data webhooks. We verify Ed25519 first and do NOT fall back to
    RSA when an `x-ghl-signature` is present (downgrade protection).
    `locationId`/`companyId` arrive in the payload, not the URL: this is the
    single Default Webhook URL GHL posts every marketplace event to (lifecycle
    INSTALL/UNINSTALL plus per-location data events), routed below by `type`.
    """
    settings = get_settings()
    body = await request.body()
    if not verify_ghl_marketplace_webhook(
        payload=body,
        ed25519_signature=x_ghl_signature,
        rsa_signature=x_wh_signature,
        ed25519_public_key_pem=settings.ghl_marketplace_public_key_ed25519,
        rsa_public_key_pem=settings.ghl_marketplace_public_key,
    ):
        scheme = "ed25519" if x_ghl_signature else "rsa" if x_wh_signature else "none"
        logger.warning("webhook.ghl.marketplace.signature_rejected", bytes=len(body), scheme=scheme)
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = await request.json()
    except ValueError:
        payload = {}

    event_type = str(payload.get("type") or "").upper()
    raw_type = str(payload.get("type") or payload.get("event") or "")
    location_id = payload.get("locationId") or payload.get("location_id")
    arq = request.app.state.arq

    if event_type == "INSTALL" and location_id:
        await arq.enqueue_job(
            "handle_ghl_install",
            payload,
            _job_id=f"ghl:install:{location_id}",
        )
    elif event_type == "UNINSTALL" and location_id:
        await arq.enqueue_job(
            "handle_ghl_uninstall",
            str(location_id),
            _job_id=f"ghl:uninstall:{location_id}",
        )
    elif location_id and raw_type:
        # Data/event webhook (ContactUpdate, OpportunityStatusUpdate, call
        # outcome, …): GHL sends these to the same Default Webhook URL signed
        # with the global key. The worker resolves the merchant from locationId
        # and routes to the lead sync / WhatsApp takeover (UC-01/02/03/04).
        # Deterministic _job_id dedups re-deliveries (GHL retries on a non-2xx):
        # prefer GHL's own event id, else fall back to a stable composite.
        await arq.enqueue_job(
            "handle_ghl_event",
            str(location_id),
            raw_type,
            payload,
            _job_id=f"ghl:event:{_ghl_event_dedup_key(payload, location_id, raw_type)}",
        )
    else:
        logger.info(
            "webhook.ghl.marketplace.ignored",
            event_type=event_type,
            has_location=bool(location_id),
        )
        return {"accepted": False, "event_type": event_type}

    logger.info(
        "webhook.ghl.marketplace.enqueued",
        event_type=event_type,
        location_id=str(location_id),
    )
    return {"accepted": True, "event_type": event_type}
