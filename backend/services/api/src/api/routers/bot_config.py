"""Bot config + agency templates — UC-10 hosts agency defaults, merchant route
applies the cascade + respects `locked_keys`.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from api.dependencies.auth import require_role
from api.dependencies.session import CurrentContext, DBSession
from config_resolver import BotConfigSchema, ConfigKey
from db import BotTemplateRepository
from db.models import BotConfig, Merchant
from shared import NotFoundError, PermissionDeniedError

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
    dependencies=[Depends(require_role("agency_admin", "agency_user"))],
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
    return _tmpl_out(updated)


# ---- Merchant config resolved view + overrides ---------------------------

@router.get("/{merchant_id}/resolved", response_model=BotConfigSchema)
async def resolved_config(
    merchant_id: UUID, session: DBSession, ctx: CurrentContext
) -> BotConfigSchema:
    _assert_merchant(ctx, merchant_id)
    from config_resolver import ConfigResolver

    resolver = ConfigResolver(session)
    resolved: dict[str, Any] = {}
    for key in ConfigKey:
        value = await resolver.resolve(key, merchant_id=merchant_id)
        _dotted_set(resolved, key.value, value)
    return BotConfigSchema.model_validate(resolved)


class OverridesIn(BaseModel):
    overrides: dict[str, Any]


@router.put("/{merchant_id}/overrides")
async def update_overrides(
    merchant_id: UUID,
    payload: OverridesIn,
    session: DBSession,
    ctx: CurrentContext,
) -> dict:
    _assert_merchant(ctx, merchant_id)
    _validate_defaults(payload.overrides)

    # Enforce agency lock: find the tenant's default template, drop any overrides
    # whose key the agency has locked.
    repo = BotTemplateRepository(session)
    templates = await repo.list_for_tenant(ctx.tenant_id)
    default_tmpl = next((t for t in templates if t.is_default), None)
    locked = set(default_tmpl.locked_keys or []) if default_tmpl else set()
    _strip_locked_keys(payload.overrides, locked)

    # Upsert the bot_configs row.
    row = (
        await session.execute(select(BotConfig).where(BotConfig.merchant_id == merchant_id))
    ).scalar_one_or_none()
    if row is None:
        row = BotConfig(merchant_id=merchant_id, overrides=payload.overrides)
        session.add(row)
    else:
        row.overrides = payload.overrides

    return {"updated": True, "locked_keys_skipped": sorted(locked)}


# ---- helpers -------------------------------------------------------------

def _tmpl_out(t) -> TemplateOut:
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
        raise PermissionDeniedError(f"Invalid config bag: {e}", error_code="invalid_config_bag")


def _validate_locked_keys(keys: list[str]) -> None:
    known = {k.value for k in ConfigKey}
    bad = [k for k in keys if k not in known]
    if bad:
        raise PermissionDeniedError(
            f"Unknown config keys: {bad}", error_code="unknown_locked_keys"
        )


def _assert_merchant(ctx, merchant_id: UUID) -> None:
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
