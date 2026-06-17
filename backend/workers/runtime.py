"""Process-wide runtime wiring for the ARQ worker.

Initialised once in `workers.settings.startup`, then handlers pull the
components they need from the ARQ context dict.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_core import (
    ActionDispatcher,
    ConversationOrchestrator,
    ConversationService,
    FtModelResolver,
    ModelRouter,
    ReplySender,
    SentimentAnalyzer,
)
from ai_core.actions import (
    BookSlotHandler,
    CancelSlotHandler,
    EscalateHumanHandler,
    MovePipelineHandler,
    RescheduleSlotHandler,
    UpdateScoreHandler,
)
from ai_core.rag import Embedder
from integrations import build_whatsapp_sender
from shared import Settings, get_logger

logger = get_logger(__name__)


class WhatsAppReplySender:
    """Bridges the 360dialog WhatsApp client to the ReplySender protocol
    used by ConversationService.

    `api_key` is the per-channel D360 key the caller already resolved from
    the integrations row (delivered by the router). `waba_base_url` is the
    per-channel host returned alongside that key — None falls back to the
    D360 default inside the client.
    """

    async def send(
        self,
        *,
        phone_number_id: str,
        api_key: str,
        to_phone: str,
        text: str,
        waba_base_url: str | None = None,
    ) -> str:
        sender = build_whatsapp_sender(
            phone_number_id=phone_number_id,
            api_key=api_key,
            waba_base_url=waba_base_url,
        )
        try:
            resp = await sender.send_text(to_phone=to_phone, text=text)
            return str((resp.get("messages") or [{}])[0].get("id", ""))
        finally:
            await sender.close()

    async def send_typing_indicator(
        self,
        *,
        phone_number_id: str,
        api_key: str,
        message_id: str,
        waba_base_url: str | None = None,
    ) -> None:
        """Read receipt + "typing…" indicator for the customer's inbound message.
        Best-effort: the caller swallows failures so a typing hiccup never blocks
        the reply."""
        sender = build_whatsapp_sender(
            phone_number_id=phone_number_id,
            api_key=api_key,
            waba_base_url=waba_base_url,
        )
        try:
            await sender.send_typing_indicator(message_id=message_id)
        finally:
            await sender.close()


@dataclass(slots=True)
class Runtime:
    settings: Settings
    conversation_service: ConversationService
    embedder: Embedder | None = None


def build_runtime(settings: Settings) -> Runtime:
    # UC-09 FT rollout — route to a tenant's deployed FT model (gated to the
    # "ft" A/B arm while a rollout experiment is running).
    router = ModelRouter(settings, ft_model_provider=FtModelResolver())
    orchestrator = ConversationOrchestrator(router)
    dispatcher = ActionDispatcher()
    sender: ReplySender = WhatsAppReplySender()
    # UC-04/05 — per-turn sentiment, routed to gpt-5-nano via the same router.
    sentiment = SentimentAnalyzer(router)

    # UC-02
    booking = BookSlotHandler(
        kek_base64=settings.integrations_kek_base64,
        ghl_client_id=settings.ghl_client_id,
        ghl_client_secret=settings.ghl_client_secret,
        reply_sender=sender,
    )
    dispatcher.register(booking.kind, booking)

    # UC-02 — reschedule / cancel an existing appointment over WhatsApp.
    reschedule = RescheduleSlotHandler(
        kek_base64=settings.integrations_kek_base64,
        ghl_client_id=settings.ghl_client_id,
        ghl_client_secret=settings.ghl_client_secret,
        reply_sender=sender,
    )
    dispatcher.register(reschedule.kind, reschedule)

    cancel = CancelSlotHandler(
        kek_base64=settings.integrations_kek_base64,
        ghl_client_id=settings.ghl_client_id,
        ghl_client_secret=settings.ghl_client_secret,
        reply_sender=sender,
    )
    dispatcher.register(cancel.kind, cancel)

    # UC-04
    move_pipeline = MovePipelineHandler(
        kek_base64=settings.integrations_kek_base64,
        ghl_client_id=settings.ghl_client_id,
        ghl_client_secret=settings.ghl_client_secret,
    )
    dispatcher.register(move_pipeline.kind, move_pipeline)

    # UC-05
    update_score = UpdateScoreHandler()
    dispatcher.register(update_score.kind, update_score)

    # Escalation — human takeover when the lead is angry / asks for a person.
    escalate = EscalateHumanHandler()
    dispatcher.register(escalate.kind, escalate)

    # UC-07 — embedder shared across conversation turns and the indexer job.
    embedder = (
        Embedder(api_key=settings.openai_api_key, model=settings.llm_model_embedding)
        if settings.openai_api_key
        else None
    )

    service = ConversationService(
        orchestrator=orchestrator,
        action_dispatcher=dispatcher,
        reply_sender=sender,
        embedder=embedder,
        sentiment=sentiment,
        kek_base64=settings.integrations_kek_base64,
    )
    return Runtime(settings=settings, conversation_service=service, embedder=embedder)
