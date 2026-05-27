"""Conversations API.

Most read traffic flows directly from the frontend to Supabase under RLS;
the backend hosts only the actions that need orchestration. Today that's
the human-reply composer: insert a `pending` row under RLS (so the merchant
can only post into their own threads), then enqueue an ARQ job that calls
the 360dialog send API and updates the row to `sent` / `failed`. Status
callbacks (delivered/read) come back through `routers/webhooks.py`.
"""

from __future__ import annotations

from datetime import datetime
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


class SendMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    client_message_id: str = Field(
        min_length=8,
        max_length=64,
        description=(
            "Caller-provided UUID. Used to deduplicate retries and to reconcile "
            "the frontend's optimistic insert with the canonical row."
        ),
    )


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
async def list_conversations(ctx: CurrentContext, session: DBSession) -> list[dict]:
    """Kept for parity — frontend usually reads conversations directly via Supabase client."""
    raise NotImplementedError("List conversations (prefer direct Supabase read)")


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: UUID, session: DBSession) -> dict:
    raise NotImplementedError("Thread with messages")


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

    msg = Message(
        id=uuid4(),
        conversation_id=conversation_id,
        merchant_id=conv.merchant_id,
        role="agent",
        direction="out",
        content=body.text,
        status="pending",
        client_message_id=body.client_message_id,
        meta={"sender_type": "human"},
    )
    session.add(msg)
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
    except Exception:  # noqa: BLE001 — worker enqueue is best-effort
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
