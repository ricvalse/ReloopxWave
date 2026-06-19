"""Conversations API.

Most read traffic flows directly from the frontend to Supabase under RLS;
the backend hosts only the actions that need orchestration. Today that's
the human-reply composer: insert a `pending` row under RLS (so the merchant
can only post into their own threads), then enqueue an ARQ job that calls
the 360dialog send API and updates the row to `sent` / `failed`. Status
callbacks (delivered/read) come back through `routers/webhooks.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.dependencies.session import CurrentContext, DBSession
from db.models.conversation import Conversation, Message
from shared import PermissionDeniedError, get_logger

router = APIRouter()
logger = get_logger(__name__)


class TemplateSendIn(BaseModel):
    """An approved WhatsApp template to send out of the 24h window (CC-WA)."""

    name: str = Field(min_length=1, max_length=512)
    language: str = "it"
    variables: list[str] = Field(default_factory=list)


class SendMessageIn(BaseModel):
    # For a template send, `text` is the human-readable preview stored on the
    # inbox row; the actual wire payload is built from `template`.
    text: str = Field(min_length=1, max_length=4096)
    client_message_id: str = Field(
        min_length=8,
        max_length=64,
        description=(
            "Caller-provided UUID. Used to deduplicate retries and to reconcile "
            "the frontend's optimistic insert with the canonical row."
        ),
    )
    # When set, the message is sent as an approved template (allowed outside the
    # 24h window); when null it's a free-text reply (24h-window gated).
    template: TemplateSendIn | None = None


class MessageOut(BaseModel):
    id: UUID
    conversation_id: UUID
    role: str
    direction: str
    content: str
    status: str
    client_message_id: str | None
    wa_message_id: str | None
    delivered_at: datetime | None
    read_at: datetime | None
    failed_at: datetime | None
    error: dict[str, Any] | None
    meta: dict[str, Any] | None = None
    created_at: datetime


class UpdateNoteIn(BaseModel):
    # Cleared note arrives as null (or empty string, normalised to null below).
    internal_note: str | None = Field(default=None, max_length=4000)


class ConversationNoteOut(BaseModel):
    id: UUID
    internal_note: str | None


@router.get("/")
async def list_conversations(ctx: CurrentContext, session: DBSession) -> list[dict[str, Any]]:
    """Backend read fallback for conversation threads.

    The frontend usually reads these directly via the Supabase client (spec
    4.4), but a real backend path is useful for server-side callers and
    non-Supabase clients. RLS on the request session already scopes the rows
    to the caller's merchant (or to the whole tenant for an agency_admin); we
    only order by recency and cap the page.
    """
    rows = (
        (
            await session.execute(
                select(Conversation)
                .order_by(Conversation.last_message_at.desc().nullslast())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return [_conv_to_dict(c) for c in rows]


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: UUID, session: DBSession) -> dict[str, Any]:
    """Return a single conversation with its messages (oldest first).

    RLS scopes the lookup, so a missing row means not-found-or-not-yours and
    we return 404 either way (never leak the existence of foreign threads).
    """
    conv = (
        await session.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    messages = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return {
        **_conv_to_dict(conv),
        "messages": [_to_out(m).model_dump(mode="json") for m in messages],
    }


@router.post("/{conversation_id}/messages", status_code=201, response_model=MessageOut)
async def send_message(
    conversation_id: UUID,
    body: SendMessageIn,
    request: Request,
    ctx: CurrentContext,
    session: DBSession,
) -> MessageOut:
    """Insert a `pending` outbound message and enqueue the WA send.

    RLS scopes the conversation lookup; merchants cannot post into a thread
    they don't own. Idempotency: a duplicate POST with the same
    (conversation_id, client_message_id) returns the existing row instead of
    a 409 — this matches the composer retry semantics where the user clicks
    "Riprova" on a previously-failed bubble.
    """
    if ctx.merchant_id is None and ctx.role != "agency_admin":
        raise PermissionDeniedError(
            "Merchant context required to send messages",
            error_code="no_merchant_context",
        )

    # RLS will already block cross-tenant reads; a NULL here means
    # not-found or not-yours, which we treat the same way externally.
    conv = (
        await session.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    # Idempotent insert: a retry POST must return the prior row, not create
    # a duplicate. The unique partial index on
    # (conversation_id, client_message_id) backs this.
    existing = (
        await session.execute(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.client_message_id == body.client_message_id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        logger.info(
            "conversations.send.idempotent_hit",
            conversation_id=str(conversation_id),
            message_id=str(existing.id),
            status=existing.status,
        )
        return _to_out(existing)

    meta: dict[str, Any] = {"sender_type": "human"}
    if body.template is not None:
        meta["kind"] = "template"
        meta["template"] = {
            "name": body.template.name,
            "language": body.template.language,
            "variables": list(body.template.variables),
        }

    msg = Message(
        id=uuid4(),
        conversation_id=conversation_id,
        merchant_id=conv.merchant_id,
        role="agent",
        direction="out",
        content=body.text,
        status="pending",
        client_message_id=body.client_message_id,
        meta=meta,
    )
    session.add(msg)

    # Auto-takeover: a human replying to a bot-active thread takes it over
    # (Amalia pattern — sending IS taking over). The idempotent path above
    # already returned, so this only runs on a genuinely new human message.
    actor = str(ctx.actor_id) if ctx.actor_id is not None else None
    if conv.auto_reply:
        conv.auto_reply = False
        conv.handoff_at = datetime.now(UTC)
        conv.handoff_reason = "manual_reply"
    conv.assigned_to = actor

    await session.flush()
    await session.commit()

    # Enqueue the worker. The service-role re-fetch happens inside the worker
    # so this request stays under the user's RLS for the insert.
    try:
        arq = request.app.state.arq
        await arq.enqueue_job(
            "send_outbound_whatsapp",
            str(msg.id),
            _job_id=f"wa:out:{msg.id}",
            _queue_name="wa:outbound",
        )
    except Exception:
        logger.exception(
            "conversations.send.enqueue_failed",
            message_id=str(msg.id),
            conversation_id=str(conversation_id),
        )

    logger.info(
        "conversations.send.accepted",
        conversation_id=str(conversation_id),
        message_id=str(msg.id),
        merchant_id=str(conv.merchant_id),
    )
    return _to_out(msg)


@router.patch("/{conversation_id}/notes", response_model=ConversationNoteOut)
async def update_note(
    conversation_id: UUID,
    body: UpdateNoteIn,
    ctx: CurrentContext,
    session: DBSession,
) -> ConversationNoteOut:
    """Set the agent's free-text internal note on a conversation.

    Same trust boundary as send_message: RLS scopes the lookup to the
    caller's merchant, so a NULL row means not-found-or-not-yours and we
    return 404 either way (never leak the existence of foreign threads).
    An empty string is normalised to NULL so "cleared" and "never set"
    are the same state. The note lives on `conversations`, which is
    published to supabase_realtime — the resulting UPDATE reconciles the
    frontend's optimistic write through the list subscription.
    """
    if ctx.merchant_id is None and ctx.role != "agency_admin":
        raise PermissionDeniedError(
            "Merchant context required to edit conversation notes",
            error_code="no_merchant_context",
        )

    conv = (
        await session.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    note = body.internal_note.strip() if body.internal_note else None
    conv.internal_note = note or None
    await session.commit()

    logger.info(
        "conversations.note.updated",
        conversation_id=str(conversation_id),
        merchant_id=str(conv.merchant_id),
        cleared=conv.internal_note is None,
    )
    return ConversationNoteOut(id=conv.id, internal_note=conv.internal_note)


class AiPauseIn(BaseModel):
    # Soft-pause duration. Default 7 days (Amalia's manual-disable window).
    hours: int = Field(default=168, ge=1, le=720)


@router.post("/{conversation_id}/ai-pause")
async def pause_ai(
    conversation_id: UUID,
    body: AiPauseIn,
    ctx: CurrentContext,
    session: DBSession,
) -> dict[str, Any]:
    """Soft-pause the bot on this thread until `now + hours` (auto-resumes).

    Unlike toggling `auto_reply`, this is time-boxed: the bot comes back on its
    own when the window elapses. Used by the inbox "Disattiva AI per…" control.
    """
    if ctx.merchant_id is None and ctx.role != "agency_admin":
        raise PermissionDeniedError("Merchant context required", error_code="no_merchant_context")
    conv = (
        await session.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    conv.ai_disabled_until = datetime.now(UTC) + timedelta(hours=body.hours)
    if ctx.actor_id is not None:
        conv.assigned_to = str(ctx.actor_id)
    await session.commit()
    logger.info(
        "conversations.ai_paused",
        conversation_id=str(conversation_id),
        hours=body.hours,
    )
    return _conv_to_dict(conv)


@router.post("/{conversation_id}/ai-resume")
async def resume_ai(
    conversation_id: UUID,
    ctx: CurrentContext,
    session: DBSession,
) -> dict[str, Any]:
    """Hand the thread back to the bot: clear the soft-pause, re-enable
    auto-reply and mark the handoff resolved. Used by the "Riattiva AI" button."""
    if ctx.merchant_id is None and ctx.role != "agency_admin":
        raise PermissionDeniedError("Merchant context required", error_code="no_merchant_context")
    conv = (
        await session.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    conv.ai_disabled_until = None
    conv.auto_reply = True
    conv.handoff_resolved_at = datetime.now(UTC)
    await session.commit()
    logger.info("conversations.ai_resumed", conversation_id=str(conversation_id))
    return _conv_to_dict(conv)


def _conv_to_dict(c: Conversation) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "merchant_id": str(c.merchant_id),
        "lead_id": str(c.lead_id) if c.lead_id else None,
        "wa_contact_phone": c.wa_contact_phone,
        "status": c.status,
        "variant_id": c.variant_id,
        "auto_reply": c.auto_reply,
        "internal_note": c.internal_note,
        "message_count": c.message_count,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
        # Drives the composer's 24h-window banner / template selector (CC-WA).
        "last_inbound_at": c.last_inbound_at.isoformat() if c.last_inbound_at else None,
        # Handoff / triage state (migration 0025).
        "ai_disabled_until": c.ai_disabled_until.isoformat() if c.ai_disabled_until else None,
        "assigned_to": c.assigned_to,
        "handoff_reason": c.handoff_reason,
        "handoff_summary": c.handoff_summary,
        "handoff_at": c.handoff_at.isoformat() if c.handoff_at else None,
        "handoff_resolved_at": c.handoff_resolved_at.isoformat() if c.handoff_resolved_at else None,
        "meta": dict(c.meta) if c.meta else None,
    }


def _to_out(m: Message) -> MessageOut:
    return MessageOut(
        id=m.id,
        conversation_id=m.conversation_id,
        role=m.role,
        direction=m.direction,
        content=m.content,
        status=m.status,
        client_message_id=m.client_message_id,
        wa_message_id=m.wa_message_id,
        delivered_at=m.delivered_at,
        read_at=m.read_at,
        failed_at=m.failed_at,
        error=m.error,
        meta=dict(m.meta) if m.meta else None,
        created_at=m.created_at,
    )
