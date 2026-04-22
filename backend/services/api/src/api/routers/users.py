from __future__ import annotations

from fastapi import APIRouter

from api.dependencies.session import CurrentContext, DBSession

router = APIRouter()


@router.get("/")
async def list_users(ctx: CurrentContext, session: DBSession) -> list[dict]:
    raise NotImplementedError("List users within tenant/merchant scope")


@router.post("/invite")
async def invite_user(ctx: CurrentContext) -> dict:
    raise NotImplementedError("Invite flow (Supabase Auth + role assignment)")
