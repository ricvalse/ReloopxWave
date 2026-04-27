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
    ModelRouter,
    ReplySender,
)
from ai_core.actions import BookSlotHandler, MovePipelineHandler, UpdateScoreHandler
from ai_core.rag import Embedder
from integrations import build_whatsapp_sender
from shared import Settings, get_logger

logger = get_logger(__name__)


class WhatsAppReplySender:
    """Bridges the 360dialog WhatsApp client to the ReplySender protocol
    used by ConversationService.

    `api_key` is the per-channel D360 key the caller already resolved from
    the integrations row. Legacy rows pass the placeholder; the factory
    falls back to the platform Partner key for those.
    """

    async def send(
        self,
        *,
        phone_number_id: str,
        api_key: str,
        to_phone: str,
        text: str,
    ) -> str:
        sender = build_whatsapp_sender(phone_number_id=phone_number_id, api_key=api_key)
        try:
            resp = await sender.send_text(to_phone=to_phone, text=text)
            return str(
                (resp.get("messages") or [{}])[0].get("id", "")
            )
        finally:
            await sender.close()


@dataclass(slots=True)
class Runtime:
    settings: Settings
    conversation_service: ConversationService
    embedder: Embedder | None = None


def build_runtime(settings: Settings) -> Runtime:
    router = ModelRouter(settings)
    orchestrator = ConversationOrchestrator(router)
    dispatcher = ActionDispatcher()
    sender: ReplySender = WhatsAppReplySender()

    # UC-02
    booking = BookSlotHandler(
        kek_base64=settings.integrations_kek_base64,
        ghl_client_id=settings.ghl_client_id,
        ghl_client_secret=settings.ghl_client_secret,
        reply_sender=sender,
    )
    dispatcher.register(booking.kind, booking)

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

    # UC-07 — embedder shared across conversation turns and the indexer job.
    embedder = (
        Embedder(api_key=settings.openai_api_key) if settings.openai_api_key else None
    )

    service = ConversationService(
        orchestrator=orchestrator,
        action_dispatcher=dispatcher,
        reply_sender=sender,
        embedder=embedder,
        kek_base64=settings.integrations_kek_base64,
    )
    return Runtime(settings=settings, conversation_service=service, embedder=embedder)
