"""End-to-end conversation turn handler — the UC-01 core.

This is the thing workers call when a WhatsApp message arrives. It owns:
  1. Resolving tenant/merchant from phone_number_id (via IntegrationRepository).
  2. Loading or creating the conversation and lead rows.
  3. Building the ConversationContext with system prompt + history.
  4. Invoking the ConversationOrchestrator.
  5. Persisting the user message and the assistant reply.
  6. Delegating side-effect actions (book_slot, move_pipeline, …) to registered handlers.

Downstream UCs (02/04/05/…) plug in by registering an ActionHandler in the
`ActionDispatcher`, not by forking this file.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from ai_core.llm import ChatMessage
from ai_core.orchestrator import (
    ConversationContext,
    ConversationOrchestrator,
    OrchestratorAction,
    OrchestratorResponse,
)
from ai_core.rag import Embedder, RAGEngine
from config_resolver import ConfigKey, ConfigResolver
from db import (
    ABRepository,
    AnalyticsRepository,
    ConversationRepository,
    IntegrationRepository,
    LeadRepository,
    MessageRepository,
    ResolvedWhatsAppIntegration,
    TenantContext,
    tenant_session,
)
from shared import get_logger

logger = get_logger(__name__)


# ---- Action dispatcher ----------------------------------------------------

class ActionHandler(Protocol):
    async def __call__(self, action: OrchestratorAction, ctx: TurnContext) -> None: ...


class ActionDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[str, ActionHandler] = {}

    def register(self, kind: str, handler: ActionHandler) -> None:
        self._handlers[kind] = handler

    async def dispatch(self, actions: list[OrchestratorAction], ctx: TurnContext) -> None:
        for action in actions:
            handler = self._handlers.get(action.kind)
            if handler is None:
                logger.debug("action.no_handler", kind=action.kind)
                continue
            try:
                await handler(action, ctx)
            except Exception as e:
                logger.warning(
                    "action.handler_failed", kind=action.kind, error=str(e), merchant_id=str(ctx.merchant_id)
                )


# ---- The turn context passed to action handlers --------------------------

@dataclass(slots=True)
class TurnContext:
    tenant_id: UUID
    merchant_id: UUID
    lead_id: UUID
    conversation_id: UUID
    lead_phone: str
    whatsapp_access_token: str
    phone_number_id: str
    whatsapp_provider: str = "meta"  # "meta" or "d360"


# ---- The sender protocol — workers inject a real WhatsApp client, tests inject a fake

class ReplySender(Protocol):
    async def send(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        to_phone: str,
        text: str,
        provider: str = "meta",
    ) -> str: ...


# ---- The entry point workers call ----------------------------------------

@dataclass(slots=True, frozen=True)
class InboundResult:
    handled: bool
    conversation_id: UUID | None = None
    reply_text: str | None = None
    reason: str | None = None


DEFAULT_SYSTEM_PROMPT = (
    "Sei un assistente conversazionale italiano per l'azienda. Rispondi in modo "
    "cortese, breve e professionale. Se la richiesta riguarda prenotazioni, proponi "
    "di prenotare. Se mancano informazioni critiche (nome, email, esigenza), "
    "chiedile in modo naturale, una alla volta. Non inventare fatti sull'azienda: "
    "se non sai qualcosa, dillo e offri di far contattare una persona."
)


class ConversationService:
    """Stateless orchestration glue. Open a fresh instance per turn, or share
    one across turns — both work; no hidden per-turn state is kept on the instance.
    """

    def __init__(
        self,
        *,
        orchestrator: ConversationOrchestrator,
        action_dispatcher: ActionDispatcher,
        reply_sender: ReplySender,
        embedder: Embedder | None = None,
        kek_base64: str,
    ) -> None:
        self._orchestrator = orchestrator
        self._dispatcher = action_dispatcher
        self._sender = reply_sender
        self._embedder = embedder
        self._kek = kek_base64

    async def handle_inbound(
        self,
        *,
        phone_number_id: str,
        from_phone: str,
        text: str,
        wa_message_id: str | None,
    ) -> InboundResult:
        # 1. Resolve tenant/merchant from phone_number_id. Uses an unscoped session
        #    because the integrations row is needed before we have a tenant context.
        resolved = await self._resolve_integration(phone_number_id)
        if resolved is None:
            logger.info("uc01.no_integration", phone_number_id=phone_number_id)
            return InboundResult(handled=False, reason="no_integration")

        # 2-6. Do the rest under a tenant-scoped session so RLS applies.
        worker_ctx = TenantContext(
            tenant_id=resolved.tenant_id,
            merchant_id=resolved.merchant_id,
            role="worker",
            actor_id=resolved.merchant_id,  # worker-owned operation
        )
        # Phase 1: DB-bound work that must commit atomically.
        # LLM and integrations calls happen INSIDE the session because the session
        # doesn't start a transaction until the first query; but we explicitly avoid
        # holding one across long HTTP calls by flushing and committing before the
        # external sends below.
        async with tenant_session(worker_ctx) as session:
            leads = LeadRepository(session)
            convs = ConversationRepository(session)
            msgs = MessageRepository(session)
            analytics = AnalyticsRepository(session)

            lead = await leads.upsert_by_phone(merchant_id=resolved.merchant_id, phone=from_phone)

            conv = await convs.get_active(
                merchant_id=resolved.merchant_id, wa_contact_phone=from_phone
            )
            if conv is None:
                # UC-09 — assign an A/B variant at conversation creation so every
                # downstream message and event carries the same variant_id.
                variant_id = await _assign_ab_variant(
                    session, merchant_id=resolved.merchant_id, lead_id=lead.id
                )
                conv = await convs.create(
                    merchant_id=resolved.merchant_id,
                    lead_id=lead.id,
                    wa_phone_number_id=phone_number_id,
                    wa_contact_phone=from_phone,
                    variant_id=variant_id,
                )

            history = await msgs.list_history(conv.id, limit=30)
            await msgs.persist_user_message(
                conversation_id=conv.id,
                merchant_id=resolved.merchant_id,
                content=text,
                wa_message_id=wa_message_id,
                variant_id=conv.variant_id,
            )

            system_prompt = await self._resolve_system_prompt(
                session=session, merchant_id=resolved.merchant_id
            )
            kb_chunks = []
            if self._embedder is not None:
                try:
                    rag = RAGEngine(session, self._embedder)
                    kb_chunks = await rag.retrieve(
                        text, merchant_id=resolved.merchant_id, top_k=5, min_score=0.7
                    )
                except Exception as e:
                    logger.warning("uc01.rag_failed", error=str(e))

            hot_threshold = await self._resolve_int(
                session, resolved.merchant_id, ConfigKey.SCORING_HOT_THRESHOLD, default=80
            )

            ctx = ConversationContext(
                merchant_id=resolved.merchant_id,
                tenant_id=resolved.tenant_id,
                lead_id=lead.id,
                lead_score=lead.score,
                hot_threshold=hot_threshold,
                system_prompt=system_prompt,
                history=[ChatMessage(role=m.role, content=m.content) for m in history],
                kb_chunks=kb_chunks,
                variant_id=conv.variant_id,
            )

            response: OrchestratorResponse = await self._orchestrator.run(ctx, text)

            await msgs.persist_assistant_message(
                conversation_id=conv.id,
                merchant_id=resolved.merchant_id,
                content=response.reply_text,
                model=response.model,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                latency_ms=response.latency_ms,
                variant_id=conv.variant_id,
            )
            await convs.touch_last_message(conv.id)

            await analytics.emit(
                tenant_id=resolved.tenant_id,
                merchant_id=resolved.merchant_id,
                event_type="message.received",
                subject_type="conversation",
                subject_id=conv.id,
                variant_id=conv.variant_id,
                properties={"role": "user", "lead_id": str(lead.id)},
            )
            await analytics.emit(
                tenant_id=resolved.tenant_id,
                merchant_id=resolved.merchant_id,
                event_type="message.replied",
                subject_type="conversation",
                subject_id=conv.id,
                variant_id=conv.variant_id,
                properties={
                    "role": "assistant",
                    "model": response.model,
                    "tokens_in": response.tokens_in,
                    "tokens_out": response.tokens_out,
                    "latency_ms": response.latency_ms,
                    "actions": [a.kind for a in response.actions],
                },
            )
            # Exit of `async with` commits the turn. If anything above raised, the
            # rollback leaves the conversation consistent — we never sent the reply.

        # Phase 2: external sends, with their own sessions where needed.
        turn_ctx = TurnContext(
            tenant_id=resolved.tenant_id,
            merchant_id=resolved.merchant_id,
            lead_id=lead.id,
            conversation_id=conv.id,
            lead_phone=from_phone,
            whatsapp_access_token=resolved.access_token,
            phone_number_id=phone_number_id,
            whatsapp_provider=resolved.provider,
        )

        await self._sender.send(
            access_token=resolved.access_token,
            phone_number_id=phone_number_id,
            to_phone=from_phone,
            text=response.reply_text,
            provider=resolved.provider,
        )

        # Action handlers run after the turn is durable and the reply is out.
        # Each handler manages its own session/transaction.
        await self._dispatcher.dispatch(response.actions, turn_ctx)

        return InboundResult(
            handled=True,
            conversation_id=conv.id,
            reply_text=response.reply_text,
        )

    # ---- helpers ----------------------------------------------------------

    async def _resolve_integration(self, phone_number_id: str) -> ResolvedWhatsAppIntegration | None:
        """Integration lookup runs without a tenant context (the lookup *is* what
        determines the tenant). We use the service-role session for this one query.
        """
        from db import session_scope

        async with session_scope() as session:
            repo = IntegrationRepository(session, kek_base64=self._kek)
            return await repo.resolve_whatsapp(phone_number_id)

    async def _resolve_system_prompt(self, *, session: Any, merchant_id: UUID) -> str:
        """Build the per-merchant system prompt from the config cascade.

        Falls back to `DEFAULT_SYSTEM_PROMPT` when nothing is configured — so
        a brand-new merchant still gets a working bot, it just sounds generic.
        """
        resolver = ConfigResolver(session)

        async def _str(key: ConfigKey) -> str | None:
            try:
                value = await resolver.resolve(key, merchant_id=merchant_id)
            except Exception:
                return None
            if isinstance(value, str) and value.strip():
                return value.strip()
            return None

        business_name = await _str(ConfigKey.BUSINESS_NAME)
        industry = await _str(ConfigKey.BUSINESS_INDUSTRY)
        description = await _str(ConfigKey.BUSINESS_DESCRIPTION)
        offer = await _str(ConfigKey.BUSINESS_OFFER)
        hours = await _str(ConfigKey.BUSINESS_HOURS)
        location = await _str(ConfigKey.BUSINESS_LOCATION)
        pricing_notes = await _str(ConfigKey.BUSINESS_PRICING_NOTES)
        website = await _str(ConfigKey.BUSINESS_WEBSITE)
        tone = await _str(ConfigKey.BOT_TONE) or "professionale-amichevole"
        language = await _str(ConfigKey.BOT_LANGUAGE) or "it"
        extras = await _str(ConfigKey.BOT_SYSTEM_PROMPT_ADDITIONS)

        has_profile = any(
            [business_name, industry, description, offer, hours, location, pricing_notes, website, extras]
        )
        if not has_profile:
            return DEFAULT_SYSTEM_PROMPT

        lines: list[str] = []
        if business_name and industry:
            lines.append(
                f"Sei un assistente conversazionale che rappresenta {business_name}, "
                f"un'attività del settore {industry}."
            )
        elif business_name:
            lines.append(f"Sei un assistente conversazionale che rappresenta {business_name}.")
        elif industry:
            lines.append(
                f"Sei un assistente conversazionale per un'attività del settore {industry}."
            )
        else:
            lines.append("Sei un assistente conversazionale per l'azienda.")

        if description:
            lines.append(f"L'attività si descrive così: {description}")
        if offer:
            lines.append(f"Offerta principale: {offer}")
        if pricing_notes:
            lines.append(f"Note sui prezzi: {pricing_notes}")
        if hours:
            lines.append(f"Orari: {hours}")
        if location:
            lines.append(f"Sede / area di copertura: {location}")
        if website:
            lines.append(f"Sito web: {website}")

        lines.append(
            f"Rispondi sempre in lingua {language} e mantieni un tono {tone}. Sii breve, "
            "cortese e concreto. Se mancano informazioni critiche (nome, email, esigenza), "
            "chiedile una alla volta. Non inventare fatti sull'attività: se non sai "
            "qualcosa, dillo e offri di far contattare una persona."
        )

        if extras:
            lines.append("Istruzioni aggiuntive dal merchant:")
            lines.append(extras)

        return "\n\n".join(lines)

    async def _resolve_int(
        self, session: Any, merchant_id: UUID, key: ConfigKey, *, default: int
    ) -> int:
        try:
            resolver = ConfigResolver(session)
            value = await resolver.resolve(key, merchant_id=merchant_id)
        except Exception:
            return default
        if isinstance(value, int):
            return value
        return default


async def _assign_ab_variant(
    session: Any, *, merchant_id: UUID, lead_id: UUID
) -> str | None:
    """Pick the oldest running experiment (if any) and hash-assign the lead."""
    try:
        ab = ABRepository(session)
        running = await ab.list_active_for_merchant(merchant_id)
        if not running:
            return None
        return await ab.assign_variant(running[0], lead_id=lead_id, merchant_id=merchant_id)
    except Exception as e:  # pragma: no cover — defensive only
        logger.warning("uc09.assignment_failed", error=str(e))
        return None
