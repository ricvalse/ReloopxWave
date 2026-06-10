"""ModelRouter — picks an LLMClient based on tenant settings and request signals.

Strategy (V1, section 6.7):
- gpt-5-mini by default
- gpt-5.2 on escalation (long context, hot lead, critical objection keywords, many turns)
- gpt-5-nano for lightweight sentiment
- fine-tuned gpt-4.1-mini per tenant when available (replaces default)
- claude-sonnet-4-6 fallback via feature flag
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from ai_core.llm import AnthropicClient, LLMClient, OpenAIClient
from shared import Settings, get_logger

logger = get_logger(__name__)

EscalationTrigger = Literal[
    "long_context", "hot_lead", "critical_objection", "many_turns", "forced"
]


class FtModelProvider(Protocol):
    """Resolves the fine-tuned model id for a tenant/merchant, if any.

    `variant_id` lets the provider gate the FT model to the "ft" arm of an A/B
    rollout (spec 6.7 — FT replaces the default via A/B, not a flag flip).
    """

    async def get(
        self, tenant_id: UUID, merchant_id: UUID, variant_id: str | None
    ) -> str | None: ...


@dataclass(slots=True, frozen=True)
class RoutingRequest:
    merchant_id: UUID
    tenant_id: UUID
    context_tokens: int
    turn_count: int
    lead_score: int
    hot_threshold: int
    escalate_keywords_matched: bool
    purpose: Literal["chat", "sentiment", "classification", "escalation"] = "chat"
    force_model: str | None = None
    variant_id: str | None = None


class ModelRouter:
    def __init__(
        self,
        settings: Settings,
        *,
        ft_model_provider: FtModelProvider | None = None,
    ) -> None:
        self._settings = settings
        self._ft_model_provider = ft_model_provider

    async def select(self, req: RoutingRequest) -> LLMClient:
        if req.force_model is not None:
            return OpenAIClient(api_key=self._settings.openai_api_key, model=req.force_model)

        if req.purpose == "sentiment":
            return OpenAIClient(
                api_key=self._settings.openai_api_key, model=self._settings.llm_model_sentiment
            )

        triggers = self._escalation_triggers(req)
        if triggers:
            logger.info(
                "routing.escalate", triggers=list(triggers), merchant_id=str(req.merchant_id)
            )
            return OpenAIClient(
                api_key=self._settings.openai_api_key, model=self._settings.llm_model_escalation
            )

        # Check per-tenant FT override before defaulting to the base chat model.
        if self._ft_model_provider is not None:
            ft_model_id = await self._ft_model_provider.get(
                req.tenant_id, req.merchant_id, req.variant_id
            )
            if ft_model_id is not None:
                return OpenAIClient(api_key=self._settings.openai_api_key, model=ft_model_id)

        return OpenAIClient(
            api_key=self._settings.openai_api_key, model=self._settings.llm_model_default
        )

    async def fallback(self) -> LLMClient | None:
        if not self._settings.anthropic_fallback_enabled or not self._settings.anthropic_api_key:
            return None
        return AnthropicClient(
            api_key=self._settings.anthropic_api_key, model=self._settings.llm_model_fallback
        )

    def _escalation_triggers(self, req: RoutingRequest) -> set[EscalationTrigger]:
        triggers: set[EscalationTrigger] = set()
        if req.purpose == "escalation":
            triggers.add("forced")
        if req.context_tokens > 4000:
            triggers.add("long_context")
        if req.lead_score >= req.hot_threshold:
            triggers.add("hot_lead")
        if req.escalate_keywords_matched:
            triggers.add("critical_objection")
        if req.turn_count >= 15:
            triggers.add("many_turns")
        return triggers
