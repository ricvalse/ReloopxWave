"""WhatsApp message templates — per-merchant CRUD + 360dialog submit/sync.

Distinct from `/bot-config/templates` (UC-10 bot prompt config). These are
Meta-approved WhatsApp message templates required to message a contact outside
the 24h customer-service window. Create lints locally, submits to 360dialog, and
persists the row as `pending_approval`; approval status is synced asynchronously
(webhook + cron). All routes are merchant-scoped.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api.dependencies.session import CurrentContext, DBSession
from db import IntegrationRepository, WhatsAppTemplateRepository
from db.models import WhatsAppTemplate
from integrations.whatsapp.d360_templates import D360TemplateClient, map_meta_status_to_local
from integrations.whatsapp.templates import (
    VALID_CATEGORIES,
    build_submit_components,
    extract_variables,
    lint_template,
)
from shared import IntegrationError, NotFoundError, PermissionDeniedError, get_logger, get_settings

router = APIRouter()
logger = get_logger(__name__)

_PURPOSE_FILTER: Any = Query(default=None, description="Filter by lifecycle purpose")
_STATUS_FILTER: Any = Query(default=None, description="Filter by local status")

_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


# ---- Models ----------------------------------------------------------------


class WhatsAppButtonIn(BaseModel):
    type: str  # QUICK_REPLY | URL | PHONE_NUMBER
    text: str = Field(max_length=25)
    url: str | None = Field(default=None, max_length=2000)
    phone_number: str | None = Field(default=None, max_length=20)
    url_example: str | None = Field(default=None, max_length=2000)


class WhatsAppTemplateCreateIn(BaseModel):
    purpose: str = Field(default="custom", max_length=64)
    category: str = Field(default="UTILITY", max_length=32)
    language: str = Field(default="it", max_length=16)
    body: str = Field(max_length=1024)
    header_type: str = Field(default="NONE", max_length=16)
    header_text: str | None = Field(default=None, max_length=60)
    header_image_url: str | None = Field(default=None, max_length=1024)
    footer: str | None = Field(default=None, max_length=60)
    buttons: list[WhatsAppButtonIn] | None = None
    # Per-slot mapping {"1": "lead.first_name"} stored for send-time resolution.
    variable_sources: dict[str, str] | None = None
    # Sample values per {{n}} used to satisfy Meta's example requirement.
    body_examples: list[str] | None = None


class WhatsAppTemplateOut(BaseModel):
    id: UUID
    name: str
    purpose: str
    category: str
    language: str
    status: str
    meta_status: str | None
    meta_quality: str | None
    rejection_reason: str | None
    body: str
    header_type: str
    header_text: str | None
    header_image_url: str | None
    footer: str | None
    buttons: list[dict[str, Any]] | None
    variables: list[str]
    variable_sources: dict[str, str]
    whatsapp_template_id: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, t: WhatsAppTemplate) -> WhatsAppTemplateOut:
        return cls(
            id=t.id,
            name=t.name,
            purpose=t.purpose,
            category=t.category,
            language=t.language,
            status=t.status,
            meta_status=t.meta_status,
            meta_quality=t.meta_quality,
            rejection_reason=t.rejection_reason,
            body=t.body,
            header_type=t.header_type,
            header_text=t.header_text,
            header_image_url=t.header_image_url,
            footer=t.footer,
            buttons=t.buttons,
            variables=list(t.variables or []),
            variable_sources=dict(t.variable_sources or {}),
            whatsapp_template_id=t.whatsapp_template_id,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )


# ---- Routes ----------------------------------------------------------------


@router.get("", response_model=list[WhatsAppTemplateOut])
async def list_templates(
    ctx: CurrentContext,
    session: DBSession,
    purpose: str | None = _PURPOSE_FILTER,
    status: str | None = _STATUS_FILTER,
) -> list[WhatsAppTemplateOut]:
    merchant_id = _require_merchant_scope(ctx)
    rows = await WhatsAppTemplateRepository(session).list_for_merchant(
        merchant_id, purpose=purpose, status=status
    )
    return [WhatsAppTemplateOut.from_model(r) for r in rows]


@router.post("", response_model=WhatsAppTemplateOut, status_code=201)
async def create_template(
    payload: WhatsAppTemplateCreateIn,
    ctx: CurrentContext,
    session: DBSession,
) -> WhatsAppTemplateOut:
    merchant_id = _require_merchant_scope(ctx)
    settings = get_settings()

    if payload.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=422, detail=f"category must be one of {VALID_CATEGORIES}")

    buttons = (
        [b.model_dump(exclude_none=True) for b in payload.buttons] if payload.buttons else None
    )
    lint_errors = lint_template(
        body=payload.body,
        category=payload.category,
        header_type=payload.header_type,
        header_text=payload.header_text,
        footer=payload.footer,
        buttons=buttons,
    )
    if lint_errors:
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"code": e.code, "message": e.message} for e in lint_errors]},
        )

    variables = extract_variables(payload.body)
    name = _generate_template_name(payload.purpose, merchant_id)
    components = build_submit_components(
        body=payload.body,
        body_examples=payload.body_examples,
        header_type=payload.header_type,
        header_text=payload.header_text,
        header_image_url=payload.header_image_url,
        footer=payload.footer,
        buttons=buttons,
    )

    integrations = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)
    wa = await integrations.resolve_whatsapp_by_merchant(merchant_id)
    if wa is None:
        raise IntegrationError(
            "WhatsApp channel not connected — connect 360dialog before creating templates",
            error_code="whatsapp_not_connected",
        )

    client = D360TemplateClient(api_key=wa.api_key, base_url=wa.waba_base_url)
    try:
        resp = await client.create_template(
            name=name,
            category=payload.category,
            language=payload.language,
            components=components,
        )
    finally:
        await client.close()

    repo = WhatsAppTemplateRepository(session)
    tpl = await repo.create(
        merchant_id=merchant_id,
        name=name,
        category=payload.category,
        language=payload.language,
        purpose=payload.purpose,
        body=payload.body,
        variables=variables,
        variable_sources=payload.variable_sources,
        header_type=payload.header_type,
        header_text=payload.header_text,
        header_image_url=payload.header_image_url,
        footer=payload.footer,
        buttons=buttons,
        status="pending_approval",
        whatsapp_template_id=(str(resp.get("id")) if resp.get("id") else None),
    )
    logger.info(
        "whatsapp_template.created",
        actor_id=str(ctx.actor_id),
        merchant_id=str(merchant_id),
        name=name,
        purpose=payload.purpose,
    )
    return WhatsAppTemplateOut.from_model(tpl)


@router.get("/{template_id}", response_model=WhatsAppTemplateOut)
async def get_template(
    template_id: UUID, ctx: CurrentContext, session: DBSession
) -> WhatsAppTemplateOut:
    _require_merchant_scope(ctx)
    tpl = await WhatsAppTemplateRepository(session).get(template_id)
    if tpl is None:
        raise NotFoundError("Template not found", template_id=str(template_id))
    return WhatsAppTemplateOut.from_model(tpl)


@router.post("/{template_id}/sync", response_model=WhatsAppTemplateOut)
async def sync_template(
    template_id: UUID, ctx: CurrentContext, session: DBSession
) -> WhatsAppTemplateOut:
    """Force a status refresh from 360dialog for one template."""
    merchant_id = _require_merchant_scope(ctx)
    settings = get_settings()
    repo = WhatsAppTemplateRepository(session)
    tpl = await repo.get(template_id)
    if tpl is None:
        raise NotFoundError("Template not found", template_id=str(template_id))

    integrations = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)
    wa = await integrations.resolve_whatsapp_by_merchant(merchant_id)
    if wa is None:
        raise IntegrationError(
            "WhatsApp channel not connected", error_code="whatsapp_not_connected"
        )

    client = D360TemplateClient(api_key=wa.api_key, base_url=wa.waba_base_url)
    try:
        status = await client.fetch_template_status(name=tpl.name)
    finally:
        await client.close()

    if status is not None:
        await repo.apply_status(
            tpl,
            local_status=map_meta_status_to_local(status.status),
            meta_status=status.status,
            quality=status.quality_score,
            rejection_reason=status.rejected_reason,
            whatsapp_template_id=status.whatsapp_template_id,
        )
    return WhatsAppTemplateOut.from_model(tpl)


@router.delete("/{template_id}", status_code=204)
async def delete_template(template_id: UUID, ctx: CurrentContext, session: DBSession) -> None:
    merchant_id = _require_merchant_scope(ctx)
    settings = get_settings()
    repo = WhatsAppTemplateRepository(session)
    tpl = await repo.get(template_id)
    if tpl is None:
        raise NotFoundError("Template not found", template_id=str(template_id))

    # Best-effort remote delete; local delete always proceeds.
    integrations = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)
    wa = await integrations.resolve_whatsapp_by_merchant(merchant_id)
    if wa is not None:
        client = D360TemplateClient(api_key=wa.api_key, base_url=wa.waba_base_url)
        try:
            await client.delete_template(name=tpl.name)
        finally:
            await client.close()
    await repo.delete(tpl)


# ---- helpers ---------------------------------------------------------------


def _require_merchant_scope(ctx: CurrentContext) -> UUID:
    if ctx.merchant_id is None:
        raise PermissionDeniedError(
            "Merchant context required for template management",
            error_code="no_merchant_context",
        )
    return ctx.merchant_id


def _base36(n: int) -> str:
    if n <= 0:
        return "0"
    out = ""
    while n > 0:
        n, r = divmod(n, 36)
        out = _BASE36[r] + out
    return out


def _generate_template_name(purpose: str, merchant_id: UUID) -> str:
    """Meta names: lowercase [a-z0-9_], unique. Base36 suffix dodges the 30-day
    name-reservation after a delete."""
    base = re.sub(r"[^a-z0-9_]", "_", purpose.lower())[:40] or "custom"
    return f"reloop_{base}_{merchant_id.hex[:8]}_{_base36(int(time.time()))}"
