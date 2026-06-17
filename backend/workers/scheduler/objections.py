"""UC-13 — objection extraction for a completed conversation.

Invoked per-conversation (enqueued from the conversation worker when it sees a
conversation close, or on a daily sweep of unclassified conversations).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from ai_core import ChatMessage, ObjectionClassifierInput, classify_objections
from ai_core.llm import OpenAIClient
from config_resolver import ConfigKey, ConfigResolver
from db import (
    AnalyticsRepository,
    ObjectionRepository,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.models import Conversation, Merchant
from shared import DomainError, get_logger

logger = get_logger(__name__)


async def extract_for_conversation(ctx: dict[str, Any], *, conversation_id: str) -> dict[str, Any]:
    settings = ctx["settings"]
    if not settings.openai_api_key:
        raise DomainError("openai key missing", error_code="no_openai_key")

    conv_uuid = UUID(conversation_id)

    # Admin session: we don't know the tenant yet.
    async with session_scope() as session:
        row = (
            await session.execute(
                select(Conversation, Merchant.tenant_id)
                .join(Merchant, Merchant.id == Conversation.merchant_id)
                .where(Conversation.id == conv_uuid)
            )
        ).one_or_none()
        if row is None:
            return {"conversation_id": conversation_id, "status": "not_found"}
        conv, tenant_id = row
        merchant_id = conv.merchant_id
        variant_id = conv.variant_id

    # Per-tenant session: load messages, classify, persist.
    tenant_ctx = TenantContext(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        role="worker",
        actor_id=merchant_id,
    )
    async with tenant_session(tenant_ctx) as session:
        objections_repo = ObjectionRepository(session)
        messages = await objections_repo.list_messages_for_classification(conv_uuid)
        if not messages:
            return {"conversation_id": conversation_id, "objections": 0}

        # Per-merchant category vocabulary (UC-13): fed into the classifier prompt.
        resolved = await ConfigResolver(session).resolve(
            ConfigKey.OBJECTION_CATEGORIES, merchant_id=merchant_id
        )
        payload = ObjectionClassifierInput(
            conversation_id=conversation_id,
            transcript=[ChatMessage(role=r, content=c) for r, c in messages],
        )
        if isinstance(resolved, list) and resolved:
            payload.categories = [str(c) for c in resolved]

        client = OpenAIClient(
            api_key=settings.openai_api_key,
            model=settings.llm_model_default,
        )
        results = await classify_objections(client, payload=payload)

        await objections_repo.replace_for_conversation(
            merchant_id=merchant_id,
            conversation_id=conv_uuid,
            items=[o.model_dump() for o in results],
            bot_variant=variant_id,
        )

        await AnalyticsRepository(session).emit(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            event_type="objections.classified",
            subject_type="conversation",
            subject_id=conv_uuid,
            properties={
                "objection_count": len(results),
                "categories": [o.category for o in results],
            },
        )

    logger.info(
        "uc13.classified",
        conversation_id=conversation_id,
        objection_count=len(results),
    )
    return {"conversation_id": conversation_id, "objections": len(results)}
