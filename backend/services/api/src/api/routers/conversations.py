from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from api.dependencies.session import CurrentContext, DBSession

router = APIRouter()


@router.get("/")
async def list_conversations(ctx: CurrentContext, session: DBSession) -> list[dict]:
    """Kept for parity — frontend usually reads conversations directly via Supabase client."""
    raise NotImplementedError("List conversations (prefer direct Supabase read)")


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: UUID, session: DBSession) -> dict:
    raise NotImplementedError("Thread with messages")
