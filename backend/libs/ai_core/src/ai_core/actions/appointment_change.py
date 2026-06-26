"""UC-02 — reschedule_slot / cancel_slot action handlers.

The orchestrator emits these when the lead asks to move or cancel an
appointment over WhatsApp. Each handler resolves the lead's upcoming
appointment(s) and acts on the single live one — if more than one is due it
asks the lead which (the safe default, so a destructive cancel is never guessed)
— applies the change via the shared `appointment_ops` service (GHL + local
mirror), and sends a WhatsApp confirmation. Best-effort like booking: a failure
is reported to the lead, never blocks the turn.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ai_core.actions.appointment_ops import cancel_appointment, reschedule_appointment
from ai_core.actions.booking import _format_human, _parse_iso, _resolve_tz
from ai_core.orchestrator import OrchestratorAction
from db import AppointmentRepository, TenantContext, tenant_session
from db.models import Appointment
from shared import get_logger

if TYPE_CHECKING:
    from ai_core.conversation_service import ReplySender, TurnContext

logger = get_logger(__name__)

_DEFAULT_DURATION = timedelta(minutes=30)


def _worker_ctx(turn_ctx: TurnContext) -> TenantContext:
    return TenantContext(
        tenant_id=turn_ctx.tenant_id,
        merchant_id=turn_ctx.merchant_id,
        role="worker",
        actor_id=turn_ctx.merchant_id,
    )


def _ambiguity_text(appointments: list[Appointment], *, verb: str) -> str:
    options = "\n".join(f"• {_format_human(a.start_at.isoformat())}" for a in appointments)
    return (
        f"Hai più appuntamenti in programma. Quale vuoi {verb}?\n{options}\n"
        "Indicami la data e procedo."
    )


class CancelSlotHandler:
    """Dispatcher target for `kind == "cancel_slot"`."""

    kind = "cancel_slot"

    def __init__(
        self,
        *,
        kek_base64: str,
        ghl_client_id: str,
        ghl_client_secret: str,
        reply_sender: ReplySender,
    ) -> None:
        self._kek = kek_base64
        self._client_id = ghl_client_id
        self._client_secret = ghl_client_secret
        self._reply_sender = reply_sender

    async def __call__(self, action: OrchestratorAction, turn_ctx: TurnContext) -> None:
        text = "Al momento non riesco a gestire la cancellazione. Ti ricontattiamo a breve."
        async with tenant_session(_worker_ctx(turn_ctx)) as session:
            appts = AppointmentRepository(session)
            upcoming = await appts.list_upcoming_for_lead(
                merchant_id=turn_ctx.merchant_id,
                lead_id=turn_ctx.lead_id,
                now=datetime.now(tz=UTC),
            )
            if not upcoming:
                text = "Non trovo appuntamenti futuri da annullare."
            elif len(upcoming) > 1:
                text = _ambiguity_text(upcoming, verb="annullare")
            else:
                appt = upcoming[0]
                when = _format_human(appt.start_at.isoformat())
                result = await cancel_appointment(
                    session,
                    appt,
                    kek=self._kek,
                    client_id=self._client_id,
                    client_secret=self._client_secret,
                    tenant_id=turn_ctx.tenant_id,
                    conversation_id=turn_ctx.conversation_id,
                )
                text = (
                    f"Ho annullato il tuo appuntamento del {when}."
                    if result.ok
                    else "Non sono riuscito ad annullare l'appuntamento. Riprova più tardi o scrivici."
                )
        await self._reply_sender.send(
            phone_number_id=turn_ctx.phone_number_id,
            api_key=turn_ctx.api_key,
            to_phone=turn_ctx.lead_phone,
            text=text,
            waba_base_url=turn_ctx.waba_base_url,
        )


class RescheduleSlotHandler:
    """Dispatcher target for `kind == "reschedule_slot"`."""

    kind = "reschedule_slot"

    def __init__(
        self,
        *,
        kek_base64: str,
        ghl_client_id: str,
        ghl_client_secret: str,
        reply_sender: ReplySender,
    ) -> None:
        self._kek = kek_base64
        self._client_id = ghl_client_id
        self._client_secret = ghl_client_secret
        self._reply_sender = reply_sender

    async def __call__(self, action: OrchestratorAction, turn_ctx: TurnContext) -> None:
        new_start_iso = action.payload.get("preferred_start_iso")
        text = "Al momento non riesco a spostare l'appuntamento. Ti ricontattiamo a breve."
        async with tenant_session(_worker_ctx(turn_ctx)) as session:
            appts = AppointmentRepository(session)
            upcoming = await appts.list_upcoming_for_lead(
                merchant_id=turn_ctx.merchant_id,
                lead_id=turn_ctx.lead_id,
                now=datetime.now(tz=UTC),
            )
            if not upcoming:
                text = "Non trovo appuntamenti futuri da spostare."
            elif not new_start_iso:
                text = "Volentieri! Per quando vuoi spostare l'appuntamento?"
            elif len(upcoming) > 1:
                text = _ambiguity_text(upcoming, verb="spostare")
            else:
                appt = upcoming[0]
                tz = _resolve_tz(appt.tz_name or "Europe/Rome")
                new_start = _parse_iso(str(new_start_iso), tz)
                if new_start is None:
                    text = "Non ho capito la nuova data/ora. Me la riscrivi?"
                else:
                    duration = (appt.end_at - appt.start_at) if appt.end_at else _DEFAULT_DURATION
                    new_end = new_start + duration
                    result = await reschedule_appointment(
                        session,
                        appt,
                        new_start=new_start,
                        new_end=new_end,
                        kek=self._kek,
                        client_id=self._client_id,
                        client_secret=self._client_secret,
                        tenant_id=turn_ctx.tenant_id,
                        conversation_id=turn_ctx.conversation_id,
                    )
                    text = (
                        f"Fatto! Ho spostato il tuo appuntamento al "
                        f"{_format_human(new_start.isoformat())}."
                        if result.ok
                        else "Non sono riuscito a spostare l'appuntamento. Riprova più tardi o scrivici."
                    )
        await self._reply_sender.send(
            phone_number_id=turn_ctx.phone_number_id,
            api_key=turn_ctx.api_key,
            to_phone=turn_ctx.lead_phone,
            text=text,
            waba_base_url=turn_ctx.waba_base_url,
        )
