"""Natural-language WhatsApp reply for a booking outcome (UC-02).

`propose_slots` and `book_slot`'s "slot taken" branch used to go out as a fixed
Italian template (`format_slot_proposal` / `format_booking_confirmation` in
`actions/booking.py`) — a second, robotic bubble the customer received right
after the AI's own reply, and labeled "Automazione" in the merchant inbox even
though no automation flow produced it. This hands the real outcome (booked /
conflict + real alternative slots / plain availability list) to a small model
instead, so the message reads like something the assistant actually wrote for
this conversation rather than a form letter.

Same nano-model, fail-open pattern as `crm_summary.py`: on any failure or
timeout this returns `None` and the caller falls back to the fixed template,
so a booking outcome is never left unsent.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from ai_core.llm import ChatMessage
from ai_core.router import ModelRouter, RoutingRequest
from shared import get_logger

logger = get_logger(__name__)

_SYSTEM = (
    "Sei l'assistente WhatsApp di un'azienda, nel mezzo di una conversazione con "
    "un cliente che vuole prenotare un appuntamento. Scrivi UN SOLO messaggio "
    "breve, naturale, in italiano — come lo scriverebbe una persona in chat: "
    "niente elenchi puntati, niente titoli, niente markdown (WhatsApp non lo "
    "interpreta), niente saluti superflui.\n"
    "Ricevi la SITUAZIONE e i DATI REALI (esito ed eventuali orari liberi): usa "
    "SOLO questi dati, non inventare né aggiungere orari che non ricevi.\n"
    "Se ci sono orari disponibili o alternativi, cita al massimo 3 orari dentro "
    "la frase (non in elenco puntato) e chiedi quale preferisce.\n"
    "Se non c'è nessun orario disponibile, dillo con naturalezza e di' che verrà "
    "ricontattato a breve.\n"
    "Rispondi SOLO con il testo del messaggio da mandare al cliente, niente altro."
)


async def compose_booking_reply(
    router: ModelRouter,
    *,
    merchant_id: UUID,
    tenant_id: UUID,
    situation: str,
    facts: str,
    timeout_s: float = 6.0,
) -> str | None:
    """The message to send, or `None` if the model call fails/times out.

    `situation` is a one-line Italian description of why this message is being
    written (proactive availability offer vs. a failed booking attempt) — it
    steers phrasing without hardcoding a template. `facts` carries the real
    outcome data (booked/not, confirmed slot, real alternatives).
    """
    req = RoutingRequest(
        merchant_id=merchant_id,
        tenant_id=tenant_id,
        context_tokens=(len(situation) + len(facts)) // 4,
        turn_count=0,
        lead_score=0,
        hot_threshold=80,
        escalate_keywords_matched=False,
        # Same economical branch as crm_summary.py: this is a short compose,
        # not reasoning, and must NOT ride the escalation triggers (long
        # context, hot lead, critical objection) that would route it to an
        # expensive model.
        purpose="sentiment",
    )
    try:
        # Inside the try too: builds the client from settings, and an
        # incomplete configuration must not block a booking confirmation.
        client = await router.select(req)
        result = await asyncio.wait_for(
            client.complete(
                messages=[
                    ChatMessage(role="system", content=_SYSTEM),
                    ChatMessage(role="user", content=f"Situazione: {situation}\n\n{facts}"),
                ],
                max_tokens=200,
            ),
            timeout=timeout_s,
        )
    except Exception as e:
        logger.warning("booking_reply.compose_failed", error=str(e), merchant_id=str(merchant_id))
        return None
    text = (result.content or "").strip()
    return text or None
