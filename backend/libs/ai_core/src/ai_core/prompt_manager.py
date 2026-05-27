"""PromptManager — resolves the system prompt for a turn (spec 6.2).

Honors A/B variant templates so the two arms of an experiment actually behave
differently (UC-09). Lookup order:

  1. A variant-specific active `system` PromptTemplate, when the conversation is
     enrolled in an experiment (`variant_id` set). This is what makes A/B real:
     each arm can run a different authored prompt.
  2. The caller-provided `fallback` (the config-cascade prompt built by
     ConversationService) — used for non-experiment conversations, or for
     variants that don't have an authored template (the control arm).

Editing a prompt is modeled as inserting a new `version` row; the repository
returns the highest active version, keeping prior versions for audit/rollback.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from db import PromptRepository
from shared import get_logger

logger = get_logger(__name__)


class PromptManager:
    def __init__(self, session: Any) -> None:
        self._repo = PromptRepository(session)

    async def resolve_system_prompt(
        self,
        *,
        merchant_id: UUID,
        variant_id: str | None,
        fallback: Callable[[], Awaitable[str]],
    ) -> str:
        if variant_id:
            body = await self._repo.get_active_body(
                merchant_id=merchant_id, kind="system", variant_id=variant_id
            )
            if body and body.strip():
                logger.info(
                    "uc09.variant_prompt_applied",
                    merchant_id=str(merchant_id),
                    variant_id=variant_id,
                )
                return body.strip()
        return await fallback()
