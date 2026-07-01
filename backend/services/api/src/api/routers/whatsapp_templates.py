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
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from api.dependencies.session import CurrentContext, DBSession
from db import IntegrationRepository, WhatsAppTemplateRepository
from db.models import WhatsAppTemplate
from integrations import SupabaseStorage
from integrations.whatsapp.d360_templates import D360TemplateClient, map_meta_status_to_local
from integrations.whatsapp.templates import (
    VALID_CATEGORIES,
    LintIssue,
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

# WhatsApp image-header limits (JPEG/PNG, 5MB per Meta). The `branding-assets`
# bucket caps at 4MB, so we enforce that lower bound here.
_MAX_HEADER_IMAGE_BYTES = 4 * 1024 * 1024
_HEADER_IMAGE_MIMES = {"image/jpeg", "image/png"}
# Long-lived signed URL: Meta fetches the header at approval (hours later) and on
# every send. ~5 years is effectively permanent; the canonical path is stored so
# the URL can be re-minted if it ever expires.
_HEADER_IMAGE_URL_TTL = 5 * 365 * 24 * 3600


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
    # Merchant-prefixed Supabase Storage path of the uploaded header image
    # (bucket `branding-assets`). The backend mints the public signed URL from it.
    header_image_path: str | None = Field(default=None, max_length=512)
    header_image_url: str | None = Field(default=None, max_length=1024)
    footer: str | None = Field(default=None, max_length=60)
    buttons: list[WhatsAppButtonIn] | None = None
    # Per-slot mapping {"1": "lead.first_name"} stored for send-time resolution.
    variable_sources: dict[str, str] | None = None
    # Sample values per {{n}} used to satisfy Meta's example requirement.
    body_examples: list[str] | None = None
    # Save locally without submitting to 360dialog/Meta (status="draft").
    as_draft: bool = False


class WhatsAppTemplateUpdateIn(BaseModel):
    """Edit a draft or rejected template. Resets it to a clean editable draft."""

    purpose: str = Field(default="custom", max_length=64)
    category: str = Field(default="UTILITY", max_length=32)
    language: str = Field(default="it", max_length=16)
    body: str = Field(max_length=1024)
    header_type: str = Field(default="NONE", max_length=16)
    header_text: str | None = Field(default=None, max_length=60)
    header_image_path: str | None = Field(default=None, max_length=512)
    header_image_url: str | None = Field(default=None, max_length=1024)
    footer: str | None = Field(default=None, max_length=60)
    buttons: list[WhatsAppButtonIn] | None = None
    variable_sources: dict[str, str] | None = None
    body_examples: list[str] | None = None


class LintIssueOut(BaseModel):
    code: str
    message: str
    severity: str
    field: str


class ValidateResultOut(BaseModel):
    valid: bool
    errors: list[LintIssueOut]
    warnings: list[LintIssueOut]


class HeaderImageOut(BaseModel):
    """Result of a header-image upload: the canonical storage path + a public
    signed URL the form can preview and the backend reuses at submit/send."""

    storage_path: str
    image_url: str


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
    header_image_path: str | None
    header_image_url: str | None
    footer: str | None
    buttons: list[dict[str, Any]] | None
    variables: list[str]
    variable_sources: dict[str, str]
    body_examples: list[str]
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
            header_image_path=t.header_image_path,
            header_image_url=t.header_image_url,
            footer=t.footer,
            buttons=t.buttons,
            variables=list(t.variables or []),
            variable_sources=dict(t.variable_sources or {}),
            body_examples=list(t.body_examples or []),
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
    _enforce_no_errors(_lint_payload(payload))

    buttons = _buttons_payload(payload)
    variables = extract_variables(payload.body)
    name = _generate_template_name(payload.purpose, merchant_id)
    repo = WhatsAppTemplateRepository(session)

    # IMAGE header: mint the public signed URL from the uploaded storage path.
    # The path is the durable artifact; the URL is Meta's header_handle/link.
    img_path = payload.header_image_path if payload.header_type == "IMAGE" else None
    header_image_url = await _resolve_header_image_url(settings, merchant_id, img_path)

    # Draft: persist locally, never touch 360dialog. The merchant submits later.
    if payload.as_draft:
        tpl = await repo.create(
            merchant_id=merchant_id,
            name=name,
            category=payload.category,
            language=payload.language,
            purpose=payload.purpose,
            body=payload.body,
            variables=variables,
            variable_sources=payload.variable_sources,
            body_examples=payload.body_examples,
            header_type=payload.header_type,
            header_text=payload.header_text,
            header_image_path=img_path,
            header_image_url=header_image_url,
            footer=payload.footer,
            buttons=buttons,
            status="draft",
        )
        logger.info(
            "whatsapp_template.draft_created",
            actor_id=str(ctx.actor_id),
            merchant_id=str(merchant_id),
            name=name,
            purpose=payload.purpose,
        )
        return WhatsAppTemplateOut.from_model(tpl)

    resp = await _submit_to_360dialog(
        session=session,
        settings=settings,
        merchant_id=merchant_id,
        name=name,
        category=payload.category,
        language=payload.language,
        components=build_submit_components(
            body=payload.body,
            body_examples=payload.body_examples,
            header_type=payload.header_type,
            header_text=payload.header_text,
            header_image_url=header_image_url,
            footer=payload.footer,
            buttons=buttons,
        ),
    )

    tpl = await repo.create(
        merchant_id=merchant_id,
        name=name,
        category=payload.category,
        language=payload.language,
        purpose=payload.purpose,
        body=payload.body,
        variables=variables,
        variable_sources=payload.variable_sources,
        body_examples=payload.body_examples,
        header_type=payload.header_type,
        header_text=payload.header_text,
        header_image_path=img_path,
        header_image_url=header_image_url,
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


@router.post("/{merchant_id}/header-image", response_model=HeaderImageOut)
async def upload_header_image(
    merchant_id: UUID,
    ctx: CurrentContext,
    file: Annotated[UploadFile, File()],
) -> HeaderImageOut:
    """Upload a template header image to Supabase Storage (bucket `branding-assets`).

    Returns the merchant-prefixed `storage_path` (the form sends this back as
    `header_image_path` on create) plus a public signed `image_url` for preview.
    Goes up via the Supabase **service role**, scoped to `{merchant_id}/...`, so
    it works both for a real merchant session and the agency-impersonation flow
    (which has no supabase-js session for a direct RLS upload).
    """
    # The write uses the service role (bypasses bucket RLS), so require an actual
    # merchant context and that it matches the path — never trust the path alone.
    if _require_merchant_scope(ctx) != merchant_id:
        raise PermissionDeniedError(
            "Cannot act on another merchant", error_code="cross_merchant_access"
        )

    data = await file.read()
    if len(data) > _MAX_HEADER_IMAGE_BYTES:
        raise PermissionDeniedError(
            "Immagine troppo grande (max 4 MB)", error_code="header_image_too_large"
        )
    content_type = (file.content_type or "").lower()
    if content_type not in _HEADER_IMAGE_MIMES:
        raise HTTPException(status_code=422, detail="Formato non supportato: usa JPEG o PNG")

    settings = get_settings()
    storage = _branding_storage(settings)
    filename = file.filename or "header"
    storage_path = f"{merchant_id}/{int(time.time())}-{_slugify(filename)}"
    await storage.upload_bytes(storage_path, data, content_type=content_type)
    image_url = await storage.create_signed_url(
        storage_path, expires_in_seconds=_HEADER_IMAGE_URL_TTL
    )

    logger.info(
        "whatsapp_template.header_image.uploaded",
        actor_id=str(ctx.actor_id),
        impersonator_id=str(ctx.impersonator_id) if ctx.impersonator_id else None,
        merchant_id=str(merchant_id),
        storage_path=storage_path,
    )
    return HeaderImageOut(storage_path=storage_path, image_url=image_url)


@router.post("/validate", response_model=ValidateResultOut)
async def validate_template(
    payload: WhatsAppTemplateCreateIn, ctx: CurrentContext, session: DBSession
) -> ValidateResultOut:
    """Server-authoritative pre-submit lint. The form calls this before sending.

    Mirrors the exact rules `create`/`submit` enforce so the merchant never
    discovers a blocking error only at submit time. Warnings are advisory.
    """
    _require_merchant_scope(ctx)
    issues = _lint_payload(payload)
    errors = [_issue_out(i) for i in issues if i.severity == "error"]
    warnings = [_issue_out(i) for i in issues if i.severity == "warning"]
    return ValidateResultOut(valid=not errors, errors=errors, warnings=warnings)


@router.put("/{template_id}", response_model=WhatsAppTemplateOut)
async def update_template(
    template_id: UUID,
    payload: WhatsAppTemplateUpdateIn,
    ctx: CurrentContext,
    session: DBSession,
) -> WhatsAppTemplateOut:
    """Edit a draft or rejected template. Meta can't edit a live template, so we
    only allow editing locally-held rows (draft | rejected) and reset them to a
    clean draft — the merchant then re-submits with one click."""
    merchant_id = _require_merchant_scope(ctx)
    if payload.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=422, detail=f"category must be one of {VALID_CATEGORIES}")
    repo = WhatsAppTemplateRepository(session)
    tpl = await repo.get(template_id)
    if tpl is None:
        raise NotFoundError("Template not found", template_id=str(template_id))
    if tpl.status not in ("draft", "rejected"):
        raise HTTPException(
            status_code=422,
            detail="only draft or rejected templates can be edited; create a new one instead",
        )
    _enforce_no_errors(_lint_payload(payload))

    settings = get_settings()
    img_path = payload.header_image_path if payload.header_type == "IMAGE" else None
    header_image_url = await _resolve_header_image_url(settings, merchant_id, img_path)

    await repo.update(
        tpl,
        category=payload.category,
        language=payload.language,
        purpose=payload.purpose,
        body=payload.body,
        variables=extract_variables(payload.body),
        variable_sources=payload.variable_sources,
        body_examples=payload.body_examples,
        header_type=payload.header_type,
        header_text=payload.header_text,
        header_image_path=img_path,
        header_image_url=header_image_url,
        footer=payload.footer,
        buttons=_buttons_payload(payload),
    )
    logger.info(
        "whatsapp_template.updated",
        actor_id=str(ctx.actor_id),
        merchant_id=str(tpl.merchant_id),
        template_id=str(template_id),
    )
    return WhatsAppTemplateOut.from_model(tpl)


@router.post("/{template_id}/submit", response_model=WhatsAppTemplateOut)
async def submit_template(
    template_id: UUID, ctx: CurrentContext, session: DBSession
) -> WhatsAppTemplateOut:
    """Submit a draft (or rejected) template to 360dialog for approval.

    A fresh Meta-safe name is minted so a previously-rejected name (reserved by
    Meta for 30 days) never blocks the resubmit."""
    merchant_id = _require_merchant_scope(ctx)
    settings = get_settings()
    repo = WhatsAppTemplateRepository(session)
    tpl = await repo.get(template_id)
    if tpl is None:
        raise NotFoundError("Template not found", template_id=str(template_id))
    if tpl.status not in ("draft", "rejected"):
        raise HTTPException(
            status_code=422, detail="only draft or rejected templates can be submitted"
        )
    _enforce_no_errors(
        lint_template(
            body=tpl.body,
            category=tpl.category,
            language=tpl.language,
            header_type=tpl.header_type,
            header_text=tpl.header_text,
            header_image_url=tpl.header_image_path or tpl.header_image_url,
            footer=tpl.footer,
            buttons=tpl.buttons,
            body_examples=list(tpl.body_examples or []),
        )
    )

    # Re-mint a fresh signed URL from the stored path so an old/expired URL never
    # reaches Meta as the header_handle.
    header_image_url = tpl.header_image_url
    if tpl.header_type == "IMAGE" and tpl.header_image_path:
        header_image_url = await _resolve_header_image_url(
            settings, merchant_id, tpl.header_image_path
        )
        tpl.header_image_url = header_image_url

    name = _generate_template_name(tpl.purpose, merchant_id)
    resp = await _submit_to_360dialog(
        session=session,
        settings=settings,
        merchant_id=merchant_id,
        name=name,
        category=tpl.category,
        language=tpl.language,
        components=build_submit_components(
            body=tpl.body,
            body_examples=list(tpl.body_examples or []),
            header_type=tpl.header_type,
            header_text=tpl.header_text,
            header_image_url=header_image_url,
            footer=tpl.footer,
            buttons=tpl.buttons,
        ),
    )
    await repo.mark_submitted(
        tpl, name=name, whatsapp_template_id=(str(resp.get("id")) if resp.get("id") else None)
    )
    logger.info(
        "whatsapp_template.submitted",
        actor_id=str(ctx.actor_id),
        merchant_id=str(merchant_id),
        template_id=str(template_id),
        name=name,
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


def _branding_storage(settings: Any) -> SupabaseStorage:
    return SupabaseStorage(
        project_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
        bucket=settings.supabase_branding_bucket,
    )


async def _resolve_header_image_url(
    settings: Any, merchant_id: UUID, storage_path: str | None
) -> str | None:
    """Mint a long-lived public signed URL for a header image storage path.

    Returns None when there's no path (non-IMAGE header). The URL is used as
    Meta's `header_handle` at submit and `image.link` at send — both require a
    publicly fetchable URL.

    The URL is signed with the Supabase **service role**, which bypasses the
    merchant-prefixed RLS on `branding-assets`. So we hard-check the path lives
    under the caller's own `{merchant_id}/` prefix before signing — otherwise a
    merchant could mint a signed URL to another tenant's object (IDOR).
    """
    if not storage_path:
        return None
    if storage_path.split("/", 1)[0] != str(merchant_id):
        raise PermissionDeniedError(
            "header image path outside merchant scope",
            error_code="header_image_cross_merchant",
        )
    signed = await _branding_storage(settings).create_signed_url(
        storage_path, expires_in_seconds=_HEADER_IMAGE_URL_TTL
    )
    return str(signed)


def _slugify(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in ".-" else "-" for c in name.lower())
    return keep.strip("-") or "header"


def _buttons_payload(
    p: WhatsAppTemplateCreateIn | WhatsAppTemplateUpdateIn,
) -> list[dict[str, Any]] | None:
    return [b.model_dump(exclude_none=True) for b in p.buttons] if p.buttons else None


def _lint_payload(p: WhatsAppTemplateCreateIn | WhatsAppTemplateUpdateIn) -> list[LintIssue]:
    return lint_template(
        body=p.body,
        category=p.category,
        language=p.language,
        header_type=p.header_type,
        header_text=p.header_text,
        # An uploaded image arrives as a storage path pre-submit; the lint only
        # checks presence (the URL is minted from it server-side).
        header_image_url=p.header_image_path or p.header_image_url,
        footer=p.footer,
        buttons=_buttons_payload(p),
        body_examples=p.body_examples,
    )


def _issue_out(i: LintIssue) -> LintIssueOut:
    return LintIssueOut(code=i.code, message=i.message, severity=i.severity, field=i.field)


def _enforce_no_errors(issues: list[LintIssue]) -> None:
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "errors": [
                    {"code": e.code, "message": e.message, "severity": e.severity, "field": e.field}
                    for e in errors
                ]
            },
        )


async def _submit_to_360dialog(
    *,
    session: DBSession,
    settings: Any,
    merchant_id: UUID,
    name: str,
    category: str,
    language: str,
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    integrations = IntegrationRepository(session, kek_base64=settings.integrations_kek_base64)
    wa = await integrations.resolve_whatsapp_by_merchant(merchant_id)
    if wa is None:
        raise IntegrationError(
            "WhatsApp channel not connected — connect 360dialog before creating templates",
            error_code="whatsapp_not_connected",
        )
    client = D360TemplateClient(api_key=wa.api_key, base_url=wa.waba_base_url)
    try:
        return await client.create_template(
            name=name, category=category, language=language, components=components
        )
    finally:
        await client.close()


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
