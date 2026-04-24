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
    """Bridges the Meta / 360dialog WhatsApp clients to the ReplySender
    protocol used by ConversationService.

    Constructs a fresh client per call because access tokens are per-merchant
    (and therefore per-message), and the provider can differ across merchants
    within the same tenant.
    """

    async def send(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        to_phone: str,
        text: str,
        provider: str = "meta",
    ) -> str:
        sender = build_whatsapp_sender(
            provider=provider, access_token=access_token, phone_number_id=phone_number_id
        )
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
