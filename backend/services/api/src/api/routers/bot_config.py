"""Bot config + agency templates — UC-10 hosts agency defaults, merchant route
applies the cascade + respects `locked_keys`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies.auth import require_role
from api.dependencies.session import CurrentContext, DBSession
from config_resolver import (
    SUGGESTED_RULES,
    TONE_PRESETS,
    BotConfigSchema,
    ConfigKey,
    ConfigResolver,
    SuggestedRules,
    TonePreset,
)
from db import BotTemplateRepository, MerchantRepository
from db.models import BotConfig, BotTemplate, Merchant
from db.session import TenantContext
from shared import NotFoundError, PermissionDeniedError, get_logger

logger = get_logger(__name__)

router = APIRouter()


# ---- Templates (UC-10) ---------------------------------------------------


class TemplateIn(BaseModel):
    name: str
    description: str | None = None
    defaults: dict[str, Any]
    locked_keys: list[str] = []
    is_default: bool = False


class TemplateOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    defaults: dict[str, Any]
    locked_keys: list[str]
    is_default: bool


@router.get(
    "/templates",
    response_model=list[TemplateOut],
    dependencies=[Depends(require_role("agency_admin"))],
)
async def list_templates(ctx: CurrentContext, session: DBSession) -> list[TemplateOut]:
    repo = BotTemplateRepository(session)
    tmpls = await repo.list_for_tenant(ctx.tenant_id)
    return [_tmpl_out(t) for t in tmpls]


@router.post(
    "/templates",
    response_model=TemplateOut,
    dependencies=[Depends(require_role("agency_admin"))],
)
async def create_template(
    payload: TemplateIn, ctx: CurrentContext, session: DBSession
) -> TemplateOut:
    # Validate the `defaults` bag through our typed schema.
    _validate_defaults(payload.defaults)
    _validate_locked_keys(payload.locked_keys)

    repo = BotTemplateRepository(session)
    tmpl = await repo.create(
        tenant_id=ctx.tenant_id,
        name=payload.name,
        description=payload.description,
        defaults=payload.defaults,
        locked_keys=payload.locked_keys,
        is_default=payload.is_default,
    )
    await _invalidate_tenant_merchants(session, ctx.tenant_id)
    return _tmpl_out(tmpl)


@router.put(
    "/templates/{template_id}",
    response_model=TemplateOut,
    dependencies=[Depends(require_role("agency_admin"))],
)
async def update_template(
    template_id: UUID,
    payload: TemplateIn,
    ctx: CurrentContext,
    session: DBSession,
) -> TemplateOut:
    _validate_defaults(payload.defaults)
    _validate_locked_keys(payload.locked_keys)
    repo = BotTemplateRepository(session)
    existing = await repo.get(template_id)
    if existing is None or existing.tenant_id != ctx.tenant_id:
        raise NotFoundError("Template not found")
    updated = await repo.update(
        template_id,
        name=payload.name,
        description=payload.description,
        defaults=payload.defaults,
        locked_keys=payload.locked_keys,
        is_default=payload.is_default,
    )
    assert updated is not None
    await _invalidate_tenant_merchants(session, ctx.tenant_id)
    return _tmpl_out(updated)


@router.delete(
    "/templates/{template_id}",
    status_code=204,
    dependencies=[Depends(require_role("agency_admin"))],
)
async def delete_template(
    template_id: UUID,
    ctx: CurrentContext,
    session: DBSession,
) -> None:
    repo = BotTemplateRepository(session)
    existing = await repo.get(template_id)
    if existing is None or existing.tenant_id != ctx.tenant_id:
        raise NotFoundError("Template not found")
    await repo.delete(template_id)
    # Removing a template (especially the default) shifts the cascade for every
    # merchant that inherited from it, so drop the whole tenant's config cache.
    await _invalidate_tenant_merchants(session, ctx.tenant_id)


class BulkApplyIn(BaseModel):
    merchant_ids: list[UUID]


class BulkApplyOut(BaseModel):
    applied: list[UUID]
    skipped: list[UUID]
    errors: list[dict[str, str]]


@router.post(
    "/templates/{template_id}/bulk-apply",
    response_model=BulkApplyOut,
    dependencies=[Depends(require_role("agency_admin"))],
)
async def bulk_apply_template(
    template_id: UUID,
    payload: BulkApplyIn,
    ctx: CurrentContext,
    session: DBSession,
) -> BulkApplyOut:
    """Link N merchant to a template (mode A — dynamic: overrides are preserved,
    future template changes propagate automatically via the cascade)."""
    repo = BotTemplateRepository(session)
    tmpl = await repo.get(template_id)
    if tmpl is None or tmpl.tenant_id != ctx.tenant_id:
        raise NotFoundError("Template not found")

    # Identify which of the requested merchant_ids belong to this tenant.
    valid_ids = set(
        (
            await session.execute(
                select(Merchant.id).where(Merchant.id.in_(payload.merchant_ids))
            )
        ).scalars().all()
    )
    requested = list(dict.fromkeys(payload.merchant_ids))  # deduplicate, preserve order

    applied: list[UUID] = []
    skipped: list[UUID] = []
    errors: list[dict[str, str]] = []

    for mid in requested:
        if mid not in valid_ids:
            skipped.append(mid)
            continue
        try:
            row = (
                await session.execute(select(BotConfig).where(BotConfig.merchant_id == mid))
            ).scalar_one_or_none()
            if row is None:
                session.add(BotConfig(merchant_id=mid, template_id=template_id, overrides={}))
            else:
                row.template_id = template_id
            applied.append(mid)
        except Exception as exc:
            errors.append({"id": str(mid), "reason": str(exc)})

    await session.flush()

    resolver = ConfigResolver(session)
    for mid in applied:
        await resolver.invalidate(mid)

    return BulkApplyOut(applied=applied, skipped=skipped, errors=errors)


class SnapshotIn(BaseModel):
    name: str
    description: str | None = None
    exclude_keys: list[str] = []


@router.post(
    "/templates/from-merchant/{merchant_id}",
    response_model=TemplateOut,
    dependencies=[Depends(require_role("agency_admin"))],
)
async def snapshot_template_from_merchant(
    merchant_id: UUID,
    payload: SnapshotIn,
    ctx: CurrentContext,
    session: DBSession,
) -> TemplateOut:
    """Create a new template by capturing the fully-resolved config of a merchant.
    Keys listed in `exclude_keys` (dotted paths) are stripped before saving."""
    merchant = await session.get(Merchant, merchant_id)
    if merchant is None or merchant.tenant_id != ctx.tenant_id:
        raise NotFoundError("Merchant not found")

    resolver = ConfigResolver(session)
    flat = await resolver.resolve_all(merchant_id=merchant_id)

    exclude_set = set(payload.exclude_keys)
    nested: dict[str, Any] = {}
    for k, v in flat.items():
        if v is None or k in exclude_set:
            continue
        _dotted_set(nested, k, v)

    repo = BotTemplateRepository(session)
    tmpl = await repo.create(
        tenant_id=ctx.tenant_id,
        name=payload.name,
        description=payload.description,
        defaults=nested,
        locked_keys=[],
        is_default=False,
    )
    return _tmpl_out(tmpl)


# ---- Persona presets + suggested rules (read-only catalogs) --------------


@router.get("/tone-presets", response_model=list[TonePreset])
async def list_tone_presets(ctx: CurrentContext) -> list[TonePreset]:
    """Curated persona presets. Applying one is a normal overrides write of
    the preset's `values` (existing `bot.*` keys)."""
    return TONE_PRESETS


@router.get("/suggested-rules", response_model=SuggestedRules)
async def list_suggested_rules(ctx: CurrentContext) -> SuggestedRules:
    """Curated do / don't phrase library for the bot-config rules editor."""
    return SUGGESTED_RULES


# ---- Merchant config resolved view + overrides ---------------------------


@router.get("/{merchant_id}/resolved", response_model=BotConfigSchema)
async def resolved_config(
    merchant_id: UUID, session: DBSession, ctx: CurrentContext
) -> BotConfigSchema:
    _assert_merchant(ctx, merchant_id)

    resolver = ConfigResolver(session)
    flat = await resolver.resolve_all(merchant_id=merchant_id)
    resolved: dict[str, Any] = {}
    for dotted_key, value in flat.items():
        _dotted_set(resolved, dotted_key, value)
    return BotConfigSchema.model_validate(resolved)


class OverridesIn(BaseModel):
    overrides: dict[str, Any]


class OverridesOut(BaseModel):
    merchant_id: UUID
    overrides: dict[str, Any]
    locked_keys: list[str]
    template_id: UUID | None = None
    # True when the caller is an agency admin impersonating this merchant. The
    # merchant portal uses it to render locked fields as editable (the agency
    # owns the lock and may override it — see `update_overrides`).
    is_impersonation: bool = False


@router.get("/{merchant_id}/overrides", response_model=OverridesOut)
async def get_overrides(merchant_id: UUID, session: DBSession, ctx: CurrentContext) -> OverridesOut:
    """Read the raw merchant overrides (what the merchant portal shows as
    'Customized' values) alongside the template's `locked_keys`.
    Used by the bot-config form to decide Inherited vs Customized vs Locked
    per field without re-implementing the cascade.
    """
    _assert_merchant(ctx, merchant_id)
    row = (
        await session.execute(select(BotConfig).where(BotConfig.merchant_id == merchant_id))
    ).scalar_one_or_none()
    overrides = dict(row.overrides or {}) if row else {}
    specific_template_id = row.template_id if row is not None else None

    tmpl = await _resolve_merchant_template(session, ctx.tenant_id, specific_template_id)
    locked = list(tmpl.locked_keys or []) if tmpl else []

    return OverridesOut(
        merchant_id=merchant_id,
        overrides=overrides,
        locked_keys=locked,
        template_id=specific_template_id,
        is_impersonation=ctx.is_impersonation,
    )


@router.put("/{merchant_id}/overrides")
async def update_overrides(
    merchant_id: UUID,
    payload: OverridesIn,
    session: DBSession,
    ctx: CurrentContext,
) -> dict[str, Any]:
    _assert_merchant(ctx, merchant_id)
    _validate_defaults(payload.overrides)

    # Enforce agency lock: use the merchant's specific template (or the tenant default),
    # drop any overrides whose key the agency has locked — UNLESS the caller is the
    # agency itself impersonating the merchant (owns the lock, may override it).
    row = (
        await session.execute(select(BotConfig).where(BotConfig.merchant_id == merchant_id))
    ).scalar_one_or_none()
    specific_template_id = row.template_id if row is not None else None
    effective_tmpl = await _resolve_merchant_template(session, ctx.tenant_id, specific_template_id)
    locked = set(effective_tmpl.locked_keys or []) if effective_tmpl else set()

    if ctx.is_impersonation:
        skipped: list[str] = []
        if locked:
            logger.info(
                "bot_config.lock_bypass",
                impersonator_id=str(ctx.impersonator_id),
                merchant_id=str(merchant_id),
                locked_keys=sorted(locked),
            )
    else:
        _strip_locked_keys(payload.overrides, locked)
        skipped = sorted(locked)

    # Upsert the bot_configs row.
    row = (
        await session.execute(select(BotConfig).where(BotConfig.merchant_id == merchant_id))
    ).scalar_one_or_none()
    if row is None:
        row = BotConfig(merchant_id=merchant_id, overrides=payload.overrides)
        session.add(row)
    else:
        row.overrides = payload.overrides

    # Invalidate the config cache for this merchant so the new values take
    # effect immediately instead of after the ~60s TTL.
    await session.flush()
    await ConfigResolver(session).invalidate(merchant_id)

    return {"updated": True, "locked_keys_skipped": skipped}


class TemplateLinkIn(BaseModel):
    template_id: UUID | None = None


@router.put(
    "/{merchant_id}/template",
    dependencies=[Depends(require_role("agency_admin"))],
)
async def set_merchant_template(
    merchant_id: UUID,
    payload: TemplateLinkIn,
    ctx: CurrentContext,
    session: DBSession,
) -> dict[str, Any]:
    """Assign (or unset) a specific template for a merchant without touching overrides."""
    merchant = await session.get(Merchant, merchant_id)
    if merchant is None or merchant.tenant_id != ctx.tenant_id:
        raise NotFoundError("Merchant not found")

    if payload.template_id is not None:
        tmpl = await session.get(BotTemplate, payload.template_id)
        if tmpl is None or tmpl.tenant_id != ctx.tenant_id:
            raise NotFoundError("Template not found")

    row = (
        await session.execute(select(BotConfig).where(BotConfig.merchant_id == merchant_id))
    ).scalar_one_or_none()
    if row is None:
        session.add(BotConfig(merchant_id=merchant_id, template_id=payload.template_id, overrides={}))
    else:
        row.template_id = payload.template_id
    await session.flush()
    await ConfigResolver(session).invalidate(merchant_id)
    return {"updated": True}


# ---- helpers -------------------------------------------------------------


async def _resolve_merchant_template(
    session: AsyncSession, tenant_id: UUID, specific_template_id: UUID | None
) -> BotTemplate | None:
    """Return the effective BotTemplate for a merchant.
    Prefers the merchant's explicit template_id; falls back to the tenant default."""
    if specific_template_id is not None:
        return await session.get(BotTemplate, specific_template_id)
    result = await session.execute(
        select(BotTemplate).where(
            BotTemplate.tenant_id == tenant_id,
            BotTemplate.is_default.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def _invalidate_tenant_merchants(session: Any, tenant_id: UUID) -> None:
    """Drop the config cache for every merchant of a tenant.

    A change to the agency's default template shifts the cascade for any
    merchant that doesn't override the affected key, so we can't target a
    single merchant — we clear all of them.
    """
    merchants = await MerchantRepository(session).list_for_tenant(tenant_id)
    resolver = ConfigResolver(session)
    for m in merchants:
        await resolver.invalidate(m.id)


def _tmpl_out(t: Any) -> TemplateOut:
    return TemplateOut(
        id=t.id,
        name=t.name,
        description=t.description,
        defaults=t.defaults or {},
        locked_keys=t.locked_keys or [],
        is_default=t.is_default,
    )


def _validate_defaults(bag: dict[str, Any]) -> None:
    try:
        BotConfigSchema.model_validate(bag)
    except Exception as e:
        raise PermissionDeniedError(
            f"Invalid config bag: {e}", error_code="invalid_config_bag"
        ) from e


def _validate_locked_keys(keys: list[str]) -> None:
    known = {k.value for k in ConfigKey}
    bad = [k for k in keys if k not in known]
    if bad:
        raise PermissionDeniedError(f"Unknown config keys: {bad}", error_code="unknown_locked_keys")


def _assert_merchant(ctx: TenantContext, merchant_id: UUID) -> None:
    if ctx.merchant_id is not None and ctx.merchant_id != merchant_id:
        raise PermissionDeniedError(
            "Cannot act on another merchant", error_code="cross_merchant_access"
        )


def _strip_locked_keys(bag: dict[str, Any], locked: set[str]) -> None:
    """Mutate `bag` to remove any key path in `locked` (dotted)."""
    for path in list(locked):
        _dotted_delete(bag, path)


def _dotted_set(bag: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = bag
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def _dotted_delete(bag: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    node: Any = bag
    for p in parts[:-1]:
        if not isinstance(node, dict) or p not in node:
            return
        node = node[p]
    if isinstance(node, dict):
        node.pop(parts[-1], None)
