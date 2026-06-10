"""DSAR — GDPR data-subject access requests for a lead's conversation PII.

Two operations, both scoped by RLS to the caller's merchant (agency admins span
their tenant):
- `GET  /dsar/leads/{lead_id}/export` — right of access: the lead's record plus
  every conversation and message, as JSON the merchant can hand to the subject.
- `POST /dsar/leads/{lead_id}/erase`  — right to erasure: delete the lead's
  conversations (messages cascade) and strip PII from the lead row, keeping a
  tombstone so aggregate analytics stay referentially intact.

Same trust boundary as the composer: RLS scopes every lookup, so a missing row
means not-found-or-not-yours and we return 404 either way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, select

from api.dependencies.session import CurrentContext, DBSession
from db.models import Lead
from db.models.conversation import Conversation, Message
from shared import PermissionDeniedError, get_logger

router = APIRouter()
logger = get_logger(__name__)


# DSAR exports/erases a data subject's PII — restrict to admins. A read-only
# `viewer` or a non-admin `merchant_user` must not export or erase customer data.
_DSAR_ROLES = ("agency_admin", "merchant_admin")


def _require_privileged(ctx: CurrentContext) -> None:
    if ctx.role not in _DSAR_ROLES:
        raise PermissionDeniedError(
            "DSAR operations require an admin role",
            error_code="role_not_allowed",
            allowed=list(_DSAR_ROLES),
        )


async def _load_lead(session: Any, lead_id: UUID) -> Lead:
    lead = (await session.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")
    return lead


@router.get("/leads/{lead_id}/export")
async def export_lead(lead_id: UUID, ctx: CurrentContext, session: DBSession) -> dict[str, Any]:
    """Right of access — return the lead plus all conversations and messages."""
    _require_privileged(ctx)
    lead = await _load_lead(session, lead_id)

    conv_rows = (
        (
            await session.execute(
                select(Conversation)
                .where(Conversation.lead_id == lead_id)
                .order_by(Conversation.started_at.asc())
            )
        )
        .scalars()
        .all()
    )
    conv_ids = [c.id for c in conv_rows]
    msg_rows: list[Message] = []
    if conv_ids:
        msg_rows = list(
            (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id.in_(conv_ids))
                    .order_by(Message.created_at.asc())
                )
            )
            .scalars()
            .all()
        )

    logger.info(
        "dsar.export",
        actor_id=str(ctx.actor_id),
        lead_id=str(lead_id),
        conversations=len(conv_rows),
        messages=len(msg_rows),
    )
    return {
        "lead": {
            "id": str(lead.id),
            "phone": lead.phone,
            "name": lead.name,
            "email": lead.email,
            "ghl_contact_id": lead.ghl_contact_id,
            "score": lead.score,
            "sentiment": lead.sentiment,
            "status": lead.status,
            "meta": dict(lead.meta) if lead.meta else None,
        },
        "conversations": [
            {
                "id": str(c.id),
                "wa_contact_phone": c.wa_contact_phone,
                "status": c.status,
                "started_at": c.started_at.isoformat() if c.started_at else None,
                "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
            }
            for c in conv_rows
        ],
        "messages": [
            {
                "id": str(m.id),
                "conversation_id": str(m.conversation_id),
                "role": m.role,
                "direction": m.direction,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msg_rows
        ],
    }


@router.post("/leads/{lead_id}/erase")
async def erase_lead(lead_id: UUID, ctx: CurrentContext, session: DBSession) -> dict[str, Any]:
    """Right to erasure — delete the lead's conversations and strip PII.

    `leads.phone` is NOT NULL and uniquely constrained per merchant, so it's
    replaced with a per-lead tombstone rather than nulled; name/email/CRM id are
    cleared. Conversations are hard-deleted (messages cascade).
    """
    _require_privileged(ctx)
    lead = await _load_lead(session, lead_id)

    result = await session.execute(
        delete(Conversation).where(
            Conversation.lead_id == lead_id, Conversation.merchant_id == lead.merchant_id
        )
    )
    conversations_deleted = getattr(result, "rowcount", 0) or 0

    lead.name = None
    lead.email = None
    lead.ghl_contact_id = None
    lead.sentiment = None
    lead.phone = f"erased:{lead.id}"
    lead.status = "erased"
    lead.meta = {
        **(lead.meta or {}),
        "erased": True,
        "erased_at": datetime.now(tz=UTC).isoformat(),
    }
    await session.commit()

    logger.info(
        "dsar.erase",
        actor_id=str(ctx.actor_id),
        lead_id=str(lead_id),
        conversations_deleted=conversations_deleted,
    )
    return {"erased": True, "lead_id": str(lead_id), "conversations_deleted": conversations_deleted}
