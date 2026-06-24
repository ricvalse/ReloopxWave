"""Window-aware outbound decisioning — the WhatsApp 24h-window enforcement point.

WhatsApp allows free-form messages only within 24h of the customer's last
inbound message. Outside that window only an approved template may be sent.
Every proactive send (no-answer, reactivation, booking reminder, first contact)
goes through `decide_outbound` so we never send free text outside the window —
the old schedulers did, which Meta would reject (reactivation at 90 days is
*always* outside the window).

`decide_outbound` is pure (no IO) so it's cheap to unit-test; `send_decision`
performs the actual send given a built WhatsApp sender.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from db import MessageRepository, ResolvedFlowStep
from integrations.whatsapp.factory import WhatsAppSender
from integrations.whatsapp.templates import build_send_components, resolve_body_params

WINDOW = timedelta(hours=24)

# mode values
MODE_TEXT = "text"
MODE_TEMPLATE = "template"
MODE_SKIP = "skip"


def is_within_24h(last_inbound_at: datetime | None, now: datetime) -> bool:
    """True when the customer messaged within the last 24h (free text allowed)."""
    if last_inbound_at is None:
        return False
    return (now - last_inbound_at) < WINDOW


@dataclass(slots=True, frozen=True)
class OutboundDecision:
    mode: str  # text | template | skip
    text: str | None = None
    template_name: str | None = None
    template_language: str | None = None
    components: list[dict[str, Any]] | None = None
    reason: str | None = None  # populated for skip (analytics) and useful for logs


def decide_outbound(
    *,
    within_window: bool,
    fallback_text: str,
    step: ResolvedFlowStep | None = None,
    context: dict[str, str] | None = None,
) -> OutboundDecision:
    """Decide how to send a proactive message respecting the 24h window.

    `fallback_text` is the scheduler's built-in/config copy used for free text.
    `step` is the resolved flow step (None → no flow configured: 'auto' policy
    with no template). `context` maps dotted source keys → values for template
    variable resolution.
    """
    ctx = context or {}
    policy = step.window_policy if step else "auto"

    # A configured-but-disabled flow/step means the merchant turned this
    # lifecycle messaging off — respect it.
    if step is not None and (not step.flow_enabled or not step.step_enabled):
        return OutboundDecision(mode=MODE_SKIP, reason="flow_disabled")

    text = (step.free_text if step and step.free_text else fallback_text) or ""

    template_ready = bool(step and step.template_name and step.template_approved)

    def _template_decision() -> OutboundDecision:
        assert step is not None and step.template_name is not None
        body_params = resolve_body_params(
            variables=step.template_variables,
            variable_mapping=step.variable_mapping,
            context=ctx,
        )
        # A template with body variables whose params can't all be resolved (no
        # mapping, or the context lacks the source) would be sent with empty
        # placeholders — Meta rejects that. Skip rather than send a broken message.
        if step.template_variables and any(p == "" for p in body_params):
            return OutboundDecision(mode=MODE_SKIP, reason="incomplete_template_mapping")
        components = build_send_components(body_params=body_params)
        return OutboundDecision(
            mode=MODE_TEMPLATE,
            template_name=step.template_name,
            template_language=step.template_language or "it",
            components=components,
        )

    if policy == "freeform_only":
        if within_window:
            return OutboundDecision(mode=MODE_TEXT, text=text)
        return OutboundDecision(mode=MODE_SKIP, reason="outside_window_freeform_only")

    if policy == "require_template":
        if template_ready:
            return _template_decision()
        return OutboundDecision(mode=MODE_SKIP, reason="no_approved_template")

    # policy == "auto" (and the no-flow default)
    if within_window:
        return OutboundDecision(mode=MODE_TEXT, text=text)
    if template_ready:
        return _template_decision()
    # Outside the window with no approved template: never send free text.
    return OutboundDecision(mode=MODE_SKIP, reason="no_template_outside_window")


async def send_decision(
    sender: WhatsAppSender, *, to_phone: str, decision: OutboundDecision
) -> str:
    """Execute a non-skip decision, returning the WhatsApp message id."""
    if decision.mode == MODE_TEXT:
        resp = await sender.send_text(to_phone=to_phone, text=decision.text or "")
    elif decision.mode == MODE_TEMPLATE:
        resp = await sender.send_template(
            to_phone=to_phone,
            template_name=decision.template_name or "",
            language=decision.template_language or "it",
            components=decision.components or [],
        )
    else:  # pragma: no cover - callers check for skip first
        raise ValueError(f"cannot send a {decision.mode} decision")
    messages = resp.get("messages") or [{}]
    return str(messages[0].get("id", ""))


async def send_and_persist_decision(
    sender: WhatsAppSender,
    *,
    to_phone: str,
    decision: OutboundDecision,
    session: AsyncSession,
    conversation_id: UUID,
    merchant_id: UUID,
    role: str = "agent",
    sender_type: str = "automation",
) -> str:
    """Send a proactive decision AND persist the matching outbound Message row.

    Every proactive send (no-answer, reactivation, booking reminder, automation
    flow) must leave a Message in the inbox so the conversation is visible and
    so the WhatsApp delivery callback (delivered/read/failed) can attach to the
    row via `wa_message_id` instead of being dropped as `row_missing`. Reuses the
    same persistence shape as the bot-reply/composer pipeline.

    Returns the WhatsApp message id (also stored on the row).
    """
    wa_message_id = await send_decision(sender, to_phone=to_phone, decision=decision)

    # Inbox content: the free text for text sends; for templates the rendered
    # fallback/free text (the human-readable copy) with the template payload kept
    # in meta so the UI can render it as a template message.
    content = decision.text or ""
    meta: dict[str, object] = {"sender_type": sender_type}
    if decision.mode == MODE_TEMPLATE:
        meta["kind"] = "template"
        meta["template"] = {
            "name": decision.template_name,
            "language": decision.template_language,
            "components": decision.components,
        }

    await MessageRepository(session).persist_outbound_message(
        conversation_id=conversation_id,
        merchant_id=merchant_id,
        content=content,
        wa_message_id=wa_message_id or None,
        role=role,
        status="sent",
        meta=meta,
    )
    return wa_message_id
