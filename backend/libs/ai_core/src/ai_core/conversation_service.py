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

from sqlalchemy import select

from ai_core.llm import ChatMessage
from ai_core.orchestrator import (
    ConversationContext,
    ConversationOrchestrator,
    OrchestratorAction,
    OrchestratorResponse,
)
from ai_core.rag import Embedder, RAGEngine
from ai_core.scoring import derive_conversation_signals
from ai_core.sentiment import SentimentAnalyzer
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


def _to_chat_history(history: list[Any]) -> list[ChatMessage]:
    """Map stored messages to LLM-payload roles.

    DB roles are broader than the LLM role set (`user|assistant|system|tool`):
    `agent` marks a business-side reply typed by a human (composer) or echoed
    from the merchant's phone. To the model those are assistant-side turns, so
    we fold `agent` into `assistant` — otherwise OpenAI rejects the request
    with a 400 (`Invalid value: 'agent'`).
    """
    return [
        ChatMessage(role="assistant" if m.role == "agent" else m.role, content=m.content)
        for m in history
    ]


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
    phone_number_id: str
    # Per-channel D360 key and base URL for outbound sends. Action handlers
    # receive these so they don't need to re-resolve the integration row.
    # `waba_base_url` is the per-channel host the router stored on the
    # integration row; None means "use the D360 default host".
    api_key: str = ""
    waba_base_url: str | None = None


# ---- The sender protocol — workers inject a real WhatsApp client, tests inject a fake

class ReplySender(Protocol):
    async def send(
        self,
        *,
        phone_number_id: str,
        api_key: str,
        to_phone: str,
        text: str,
        waba_base_url: str | None = None,
    ) -> str: ...


# ---- The entry point workers call ----------------------------------------

@dataclass(slots=True, frozen=True)
class InboundResult:
    handled: bool
    conversation_id: UUID | None = None
    reply_text: str | None = None
    reason: str | None = None


@dataclass(slots=True, frozen=True)
class PhoneEchoResult:
    handled: bool
    conversation_id: UUID | None = None
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
        sentiment: SentimentAnalyzer | None = None,
        kek_base64: str,
    ) -> None:
        self._orchestrator = orchestrator
        self._dispatcher = action_dispatcher
        self._sender = reply_sender
        self._embedder = embedder
        self._sentiment = sentiment
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
        # Phase 1: persist the inbound message in its OWN transaction, so it is
        # durable regardless of what happens to the reply. A failure while
        # generating the reply (LLM 400/timeout, RAG, network) must never lose
        # the customer's message. Idempotent on `wa_message_id`: a redelivered
        # webhook / retried job reuses the existing row instead of duplicating.
        async with tenant_session(worker_ctx) as session:
            leads = LeadRepository(session)
            convs = ConversationRepository(session)
            msgs = MessageRepository(session)
            analytics = AnalyticsRepository(session)

            already_persisted = await msgs.find_by_wa_message_id(wa_message_id)

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

            # History for the LLM = turns BEFORE this inbound. On a retry the
            # inbound is already stored, so exclude it explicitly by wa_message_id.
            history = await msgs.list_history(conv.id, limit=30)
            history = [m for m in history if m.wa_message_id != wa_message_id]

            # Auto-reply gate: AND of merchant master + per-thread takeover.
            merchant_auto_reply = await self._resolve_bool(
                session, resolved.merchant_id, ConfigKey.BOT_AUTO_REPLY_ENABLED, default=False
            )
            auto_reply_on = bool(merchant_auto_reply and conv.auto_reply)

            if already_persisted is None:
                await msgs.persist_user_message(
                    conversation_id=conv.id,
                    merchant_id=resolved.merchant_id,
                    content=text,
                    wa_message_id=wa_message_id,
                    variant_id=conv.variant_id,
                )
                received_props: dict[str, Any] = {"role": "user", "lead_id": str(lead.id)}
                if not auto_reply_on:
                    received_props["auto_reply_skipped"] = True
                    received_props["reason"] = (
                        "merchant_off" if not merchant_auto_reply else "conversation_off"
                    )
                await analytics.emit(
                    tenant_id=resolved.tenant_id,
                    merchant_id=resolved.merchant_id,
                    event_type="message.received",
                    subject_type="conversation",
                    subject_id=conv.id,
                    variant_id=conv.variant_id,
                    properties=received_props,
                )
            await convs.touch_last_message(conv.id)

            # Capture scalars + the prepared history while the session is open;
            # the ORM objects detach after the commit below.
            conv_id = conv.id
            conv_variant_id = conv.variant_id
            lead_id = lead.id
            lead_score = lead.score
            lead_name = lead.name
            lead_email = lead.email
            chat_history = _to_chat_history(history)

            if not auto_reply_on:
                logger.info(
                    "uc01.auto_reply_skipped",
                    conversation_id=str(conv_id),
                    merchant_id=str(resolved.merchant_id),
                    merchant_auto_reply=merchant_auto_reply,
                    conversation_auto_reply=conv.auto_reply,
                )
            # Exit of `async with` commits the inbound (and the skip-path analytics).

        if not auto_reply_on:
            return InboundResult(
                handled=True,
                conversation_id=conv_id,
                reply_text=None,
                reason="auto_reply_off",
            )

        # Phase 2: generate + persist the reply in a SEPARATE transaction. If the
        # LLM or any step here fails, only the reply rolls back — the inbound
        # above is already committed and the job can be retried safely.
        async with tenant_session(worker_ctx) as session:
            convs = ConversationRepository(session)
            msgs = MessageRepository(session)
            analytics = AnalyticsRepository(session)

            system_prompt = await self._resolve_system_prompt(
                session=session, merchant_id=resolved.merchant_id, variant_id=conv_variant_id
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
                lead_id=lead_id,
                lead_score=lead_score,
                hot_threshold=hot_threshold,
                system_prompt=system_prompt,
                history=chat_history,
                kb_chunks=kb_chunks,
                variant_id=conv_variant_id,
            )

            response: OrchestratorResponse = await self._orchestrator.run(ctx, text)

            # Sentiment (UC-04 input / UC-05 signal): cheap gpt-5-nano call on the
            # inbound text. Best-effort — never blocks the reply.
            sentiment: str | None = None
            if self._sentiment is not None:
                sentiment = await self._sentiment.analyze(
                    merchant_id=resolved.merchant_id,
                    tenant_id=resolved.tenant_id,
                    text=text,
                )
                if lead_id is not None and sentiment:
                    await LeadRepository(session).update_sentiment(
                        lead_id, sentiment=sentiment
                    )

            await msgs.persist_assistant_message(
                conversation_id=conv_id,
                merchant_id=resolved.merchant_id,
                content=response.reply_text,
                model=response.model,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                latency_ms=response.latency_ms,
                variant_id=conv_variant_id,
            )
            await convs.touch_last_message(conv_id)

            await analytics.emit(
                tenant_id=resolved.tenant_id,
                merchant_id=resolved.merchant_id,
                event_type="message.replied",
                subject_type="conversation",
                subject_id=conv_id,
                variant_id=conv_variant_id,
                properties={
                    "role": "assistant",
                    "model": response.model,
                    "tokens_in": response.tokens_in,
                    "tokens_out": response.tokens_out,
                    "latency_ms": response.latency_ms,
                    "actions": [a.kind for a in response.actions],
                },
            )
            # Exit of `async with` commits the reply.

        # Phase 3: external sends, with their own sessions where needed.
        turn_ctx = TurnContext(
            tenant_id=resolved.tenant_id,
            merchant_id=resolved.merchant_id,
            lead_id=lead_id,
            conversation_id=conv_id,
            lead_phone=from_phone,
            phone_number_id=phone_number_id,
            api_key=resolved.api_key,
            waba_base_url=resolved.waba_base_url,
        )

        await self._sender.send(
            phone_number_id=phone_number_id,
            api_key=resolved.api_key,
            to_phone=from_phone,
            text=response.reply_text,
            waba_base_url=resolved.waba_base_url,
        )

        # UC-05 — always-on cumulative scoring. Derive behavioural signals from
        # accumulated state (name/email on file, engagement, sentiment, booking
        # intent) and merge with any content signals the LLM reported this turn,
        # then ensure exactly one update_score action carries the merged set.
        from ai_core.actions.scoring import derive_signals_from_llm_payload

        llm_signals: dict[str, bool] = {}
        for a in response.actions:
            if a.kind == "update_score":
                llm_signals.update(derive_signals_from_llm_payload(a.payload))
        merged_signals = derive_conversation_signals(
            has_name=bool(lead_name),
            has_email=bool(lead_email),
            turn_count=len(chat_history) + 1,
            sentiment=sentiment,
            asked_for_booking=any(a.kind == "book_slot" for a in response.actions),
            llm_signals=llm_signals,
        )
        actions = _with_score_action(response.actions, merged_signals)

        # Action handlers run after the turn is durable and the reply is out.
        # Each handler manages its own session/transaction.
        await self._dispatcher.dispatch(actions, turn_ctx)

        return InboundResult(
            handled=True,
            conversation_id=conv_id,
            reply_text=response.reply_text,
        )

    async def handle_phone_app_echo(
        self,
        *,
        phone_number_id: str,
        customer_phone: str,
        text: str,
        wa_message_id: str,
    ) -> PhoneEchoResult:
        """Persist a message the merchant typed on their phone Business App.

        Only fires for channels onboarded in 360dialog Coexistence mode; for
        classic API channels this code path is never reached. We mirror the
        inbound-side resolution (integration → tenant/lead/conversation) but
        skip the LLM orchestrator entirely — the customer has already received
        the reply on WhatsApp, this is purely a UI-mirror write.

        Idempotent on `wa_message_id`: if the row already exists we return
        without writing, so 360dialog retries are safe.
        """
        resolved = await self._resolve_integration(phone_number_id)
        if resolved is None:
            return PhoneEchoResult(handled=False, reason="no_integration")

        worker_ctx = TenantContext(
            tenant_id=resolved.tenant_id,
            merchant_id=resolved.merchant_id,
            role="worker",
            actor_id=resolved.merchant_id,
        )
        async with tenant_session(worker_ctx) as session:
            from db.models.conversation import Message as _Message  # avoid top-level cycle

            existing_id = (
                await session.execute(
                    select(_Message.conversation_id).where(
                        _Message.wa_message_id == wa_message_id
                    )
                )
            ).scalar_one_or_none()
            if existing_id is not None:
                return PhoneEchoResult(
                    handled=True,
                    conversation_id=existing_id,
                    reason="already_persisted",
                )

            leads = LeadRepository(session)
            convs = ConversationRepository(session)
            msgs = MessageRepository(session)

            lead = await leads.upsert_by_phone(
                merchant_id=resolved.merchant_id, phone=customer_phone
            )
            conv = await convs.get_active(
                merchant_id=resolved.merchant_id, wa_contact_phone=customer_phone
            )
            if conv is None:
                # First contact for this peer was the merchant texting them from
                # the phone — open the thread so the UI shows it. No A/B variant
                # is assigned: variants gate orchestrator behaviour, which echoes
                # bypass.
                conv = await convs.create(
                    merchant_id=resolved.merchant_id,
                    lead_id=lead.id,
                    wa_phone_number_id=phone_number_id,
                    wa_contact_phone=customer_phone,
                    variant_id=None,
                )

            await msgs.persist_phone_echo_message(
                conversation_id=conv.id,
                merchant_id=resolved.merchant_id,
                content=text,
                wa_message_id=wa_message_id,
            )
            await convs.touch_last_message(conv.id)

        logger.info(
            "uc01.phone_echo.persisted",
            phone_number_id=phone_number_id,
            conversation_id=str(conv.id),
            wa_message_id=wa_message_id,
        )
        return PhoneEchoResult(handled=True, conversation_id=conv.id, reason="persisted")

    # ---- helpers ----------------------------------------------------------

    async def _resolve_integration(self, phone_number_id: str) -> ResolvedWhatsAppIntegration | None:
        """Integration lookup runs without a tenant context (the lookup *is* what
        determines the tenant). We use the service-role session for this one query.
        """
        from db import session_scope

        async with session_scope() as session:
            repo = IntegrationRepository(session, kek_base64=self._kek)
            return await repo.resolve_whatsapp(phone_number_id)

    async def _resolve_system_prompt(
        self, *, session: Any, merchant_id: UUID, variant_id: str | None = None
    ) -> str:
        """Resolve the system prompt for this turn (UC-09 aware).

        Delegates to `PromptManager`: when the conversation is enrolled in an
        A/B experiment and the assigned variant has an authored `system`
        template, that template's body is used — this is what makes the two
        arms behave differently. Otherwise the config-cascade prompt below is
        used as the fallback.
        """
        from ai_core.prompt_manager import PromptManager

        manager = PromptManager(session)
        return await manager.resolve_system_prompt(
            merchant_id=merchant_id,
            variant_id=variant_id,
            fallback=lambda: self._cascade_system_prompt(
                session=session, merchant_id=merchant_id
            ),
        )

    async def _cascade_system_prompt(self, *, session: Any, merchant_id: UUID) -> str:
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

    async def _resolve_bool(
        self, session: Any, merchant_id: UUID, key: ConfigKey, *, default: bool
    ) -> bool:
        try:
            resolver = ConfigResolver(session)
            value = await resolver.resolve(key, merchant_id=merchant_id)
        except Exception:
            return default
        if isinstance(value, bool):
            return value
        return default


def _with_score_action(
    actions: list[OrchestratorAction], signals: dict[str, bool]
) -> list[OrchestratorAction]:
    """Ensure a single update_score action carries the merged signals.

    Merges into the LLM's update_score action if it emitted one, else appends a
    fresh one. With no signals at all we leave the action list untouched (the
    handler would be a no-op anyway).
    """
    if not signals:
        return actions
    out: list[OrchestratorAction] = []
    found = False
    for a in actions:
        if a.kind == "update_score":
            found = True
            payload = dict(a.payload)
            payload["signals"] = signals
            out.append(OrchestratorAction(kind="update_score", payload=payload))
        else:
            out.append(a)
    if not found:
        out.append(OrchestratorAction(kind="update_score", payload={"signals": signals}))
    return out


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
