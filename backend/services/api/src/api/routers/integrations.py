from __future__ import annotations

from fastapi import APIRouter

from api.dependencies.session import CurrentContext, DBSession

router = APIRouter()


@router.post("/ghl/oauth/start")
async def ghl_oauth_start(ctx: CurrentContext) -> dict:
    raise NotImplementedError("Return GHL OAuth authorise URL with signed state")


@router.get("/ghl/oauth/callback")
async def ghl_oauth_callback(code: str, state: str, session: DBSession) -> dict:
    raise NotImplementedError("Exchange code, encrypt token, persist")


@router.post("/whatsapp/verify")
async def whatsapp_verify(ctx: CurrentContext) -> dict:
    raise NotImplementedError("Meta phone number verification flow")


@router.get("/status")
async def integration_status(ctx: CurrentContext, session: DBSession) -> dict:
    raise NotImplementedError("Per-merchant integration health check")
