"""Merchant content endpoints — store policies, FAQ, bot corrections.

Like the KB router, these are merchant-scoped content (not agency config), so
access is gated by `_assert_merchant_scope` rather than a role: a merchant user
manages their own, an agency admin manages them while impersonating. FAQ writes
enqueue `catalog_reindex` so the RAG corpus stays in sync. (The product catalog
was removed — bookable offerings live in `services`, product info in the KB.)
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.dependencies.session import CurrentContext, DBSession
from db import (
    BotCorrectionRepository,
    FaqRepository,
    StorePolicyRepository,
)
from db.session import TenantContext
from shared import NotFoundError, PermissionDeniedError, get_logger

logger = get_logger(__name__)

router = APIRouter()

_FAQ_MAX_ENTRIES = 50
# Active corrections are loaded + scored on every turn, so keep the set bounded.
_CORRECTION_MAX_ACTIVE = 200


# ---- Schemas -------------------------------------------------------------


class FaqIn(BaseModel):
    question: str = Field(max_length=300)
    answer: str = Field(max_length=1000)
    category: str | None = Field(default=None, max_length=120)
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True


class FaqOut(BaseModel):
    id: UUID
    question: str
    answer: str
    category: str | None
    sort_order: int
    is_active: bool


class CustomPolicy(BaseModel):
    title: str = Field(max_length=120)
    body: str = Field(max_length=4000)


class PolicyIn(BaseModel):
    shipping_info: str | None = Field(default=None, max_length=4000)
    return_policy: str | None = Field(default=None, max_length=4000)
    payment_methods: str | None = Field(default=None, max_length=4000)
    exchange_policy: str | None = Field(default=None, max_length=4000)
    warranty_info: str | None = Field(default=None, max_length=4000)
    contact_info: str | None = Field(default=None, max_length=4000)
    custom_policies: list[CustomPolicy] = Field(default_factory=list, max_length=20)


class PolicyOut(PolicyIn):
    pass


class CorrectionIn(BaseModel):
    trigger_message: str = Field(max_length=4000)
    original_response: str = Field(max_length=8000)
    corrected_response: str = Field(max_length=8000)
    context: str | None = Field(default=None, max_length=2000)


class CorrectionPatch(BaseModel):
    corrected_response: str | None = Field(default=None, max_length=8000)
    is_active: bool | None = None


class CorrectionOut(BaseModel):
    id: UUID
    trigger_message: str
    original_response: str
    corrected_response: str
    context: str | None
    is_active: bool
    created_at: datetime


# ---- FAQ -----------------------------------------------------------------


@router.get("/{merchant_id}/faq", response_model=list[FaqOut])
async def list_faq(merchant_id: UUID, session: DBSession, ctx: CurrentContext) -> list[FaqOut]:
    _assert_merchant_scope(ctx, merchant_id)
    entries = await FaqRepository(session).list_for_merchant(merchant_id)
    return [_faq_out(e) for e in entries]


@router.post("/{merchant_id}/faq", response_model=FaqOut)
async def create_faq(
    merchant_id: UUID, payload: FaqIn, request: Request, session: DBSession, ctx: CurrentContext
) -> FaqOut:
    _assert_merchant_scope(ctx, merchant_id)
    repo = FaqRepository(session)
    existing = await repo.list_for_merchant(merchant_id)
    if len(existing) >= _FAQ_MAX_ENTRIES:
        raise PermissionDeniedError(
            f"Max {_FAQ_MAX_ENTRIES} FAQ entries reached", error_code="faq_limit_reached"
        )
    entry = await repo.create(
        merchant_id=merchant_id,
        question=payload.question,
        answer=payload.answer,
        category=payload.category,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    await _enqueue_reindex(request, merchant_id)
    return _faq_out(entry)


@router.put("/{merchant_id}/faq/{faq_id}", response_model=FaqOut)
async def update_faq(
    merchant_id: UUID,
    faq_id: UUID,
    payload: FaqIn,
    request: Request,
    session: DBSession,
    ctx: CurrentContext,
) -> FaqOut:
    _assert_merchant_scope(ctx, merchant_id)
    repo = FaqRepository(session)
    existing = await repo.get(faq_id)
    if existing is None or existing.merchant_id != merchant_id:
        raise NotFoundError("FAQ entry not found")
    entry = await repo.update(
        faq_id,
        question=payload.question,
        answer=payload.answer,
        category=payload.category,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    assert entry is not None
    await _enqueue_reindex(request, merchant_id)
    return _faq_out(entry)


@router.delete("/{merchant_id}/faq/{faq_id}")
async def delete_faq(
    merchant_id: UUID, faq_id: UUID, request: Request, session: DBSession, ctx: CurrentContext
) -> dict[str, Any]:
    _assert_merchant_scope(ctx, merchant_id)
    repo = FaqRepository(session)
    existing = await repo.get(faq_id)
    if existing is None or existing.merchant_id != merchant_id:
        raise NotFoundError("FAQ entry not found")
    await repo.delete(faq_id)
    await _enqueue_reindex(request, merchant_id)
    return {"deleted": True, "id": str(faq_id)}


# ---- Policies (one document per merchant) --------------------------------


@router.get("/{merchant_id}/policies", response_model=PolicyOut)
async def get_policies(merchant_id: UUID, session: DBSession, ctx: CurrentContext) -> PolicyOut:
    _assert_merchant_scope(ctx, merchant_id)
    row = await StorePolicyRepository(session).get_for_merchant(merchant_id)
    if row is None:
        return PolicyOut()
    return PolicyOut(
        shipping_info=row.shipping_info,
        return_policy=row.return_policy,
        payment_methods=row.payment_methods,
        exchange_policy=row.exchange_policy,
        warranty_info=row.warranty_info,
        contact_info=row.contact_info,
        custom_policies=[CustomPolicy(**c) for c in (row.custom_policies or [])],
    )


@router.put("/{merchant_id}/policies", response_model=PolicyOut)
async def update_policies(
    merchant_id: UUID, payload: PolicyIn, session: DBSession, ctx: CurrentContext
) -> PolicyOut:
    _assert_merchant_scope(ctx, merchant_id)
    await StorePolicyRepository(session).upsert(
        merchant_id=merchant_id,
        shipping_info=payload.shipping_info,
        return_policy=payload.return_policy,
        payment_methods=payload.payment_methods,
        exchange_policy=payload.exchange_policy,
        warranty_info=payload.warranty_info,
        contact_info=payload.contact_info,
        custom_policies=[c.model_dump() for c in payload.custom_policies],
    )
    return PolicyOut(**payload.model_dump())


# ---- Corrections (playground response-fix loop, UC-08) -------------------


@router.get("/{merchant_id}/corrections", response_model=list[CorrectionOut])
async def list_corrections(
    merchant_id: UUID, session: DBSession, ctx: CurrentContext
) -> list[CorrectionOut]:
    _assert_merchant_scope(ctx, merchant_id)
    rows = await BotCorrectionRepository(session).list_for_merchant(merchant_id)
    return [_correction_out(r) for r in rows]


@router.post("/{merchant_id}/corrections", response_model=CorrectionOut)
async def create_correction(
    merchant_id: UUID, payload: CorrectionIn, session: DBSession, ctx: CurrentContext
) -> CorrectionOut:
    _assert_merchant_scope(ctx, merchant_id)
    repo = BotCorrectionRepository(session)
    active = await repo.list_for_merchant(merchant_id, active_only=True)
    if len(active) >= _CORRECTION_MAX_ACTIVE:
        raise PermissionDeniedError(
            f"Max {_CORRECTION_MAX_ACTIVE} active corrections reached",
            error_code="correction_limit_reached",
        )
    row = await repo.create(
        merchant_id=merchant_id,
        trigger_message=payload.trigger_message,
        original_response=payload.original_response,
        corrected_response=payload.corrected_response,
        context=payload.context,
    )
    return _correction_out(row)


@router.patch("/{merchant_id}/corrections/{correction_id}", response_model=CorrectionOut)
async def update_correction(
    merchant_id: UUID,
    correction_id: UUID,
    payload: CorrectionPatch,
    session: DBSession,
    ctx: CurrentContext,
) -> CorrectionOut:
    _assert_merchant_scope(ctx, merchant_id)
    repo = BotCorrectionRepository(session)
    existing = await repo.get(correction_id)
    if existing is None or existing.merchant_id != merchant_id:
        raise NotFoundError("Correction not found")
    fields = payload.model_dump(exclude_none=True)
    row = await repo.update(correction_id, **fields) if fields else existing
    assert row is not None
    return _correction_out(row)


@router.delete("/{merchant_id}/corrections/{correction_id}")
async def delete_correction(
    merchant_id: UUID, correction_id: UUID, session: DBSession, ctx: CurrentContext
) -> dict[str, Any]:
    _assert_merchant_scope(ctx, merchant_id)
    repo = BotCorrectionRepository(session)
    existing = await repo.get(correction_id)
    if existing is None or existing.merchant_id != merchant_id:
        raise NotFoundError("Correction not found")
    await repo.delete(correction_id)
    return {"deleted": True, "id": str(correction_id)}


# ---- helpers -------------------------------------------------------------


async def _enqueue_reindex(request: Request, merchant_id: UUID) -> None:
    arq = request.app.state.arq
    await arq.enqueue_job(
        "catalog_reindex",
        str(merchant_id),
        _job_id=f"catalog:reindex:{merchant_id}:{int(time.time())}",
    )


def _assert_merchant_scope(ctx: TenantContext, merchant_id: UUID) -> None:
    if ctx.merchant_id is not None and ctx.merchant_id != merchant_id:
        raise PermissionDeniedError(
            "Cannot act on another merchant", error_code="cross_merchant_access"
        )


def _faq_out(e: Any) -> FaqOut:
    return FaqOut(
        id=e.id,
        question=e.question,
        answer=e.answer,
        category=e.category,
        sort_order=e.sort_order,
        is_active=e.is_active,
    )


def _correction_out(c: Any) -> CorrectionOut:
    return CorrectionOut(
        id=c.id,
        trigger_message=c.trigger_message,
        original_response=c.original_response,
        corrected_response=c.corrected_response,
        context=c.context,
        is_active=c.is_active,
        created_at=c.created_at,
    )
