"""Ripresa alla riapertura — risponde alle domande rimaste sospese fuori orario.

Il pezzo che rende vera la promessa fatta al cliente con il messaggio di
cortesia. Fuori dagli orari il pipeline di conversazione non genera il turno:
marca la conversazione (`conversations.off_hours_pending_at`) e si ferma.
Questo sweep, ogni cinque minuti, cerca le conversazioni marcate il cui
merchant è tornato dentro i propri orari e genera **adesso** la risposta che
allora non poteva dare.

**Perché uno sweep e non un job differito.** L'alternativa naturale sarebbe
accodare, al momento della chiusura, un job arq con `_defer_until=prossima
apertura`. È stata scartata per tre ragioni concrete, tutte già emerse in
questo repo:

  * l'attesa non è breve — da una notte a un intero fine settimana lungo — e
    per tutto quel tempo il promemoria vivrebbe **solo** in Redis: un reset
    dell'istanza e le risposte promesse svaniscono senza lasciare traccia;
  * il momento dell'apertura **cambia**. Il merchant modifica gli orari, passa
    da `custom` a `business_hours`, aggiunge una chiusura straordinaria. Un job
    già accodato porta con sé un orario deciso ieri e si sveglia quando il
    negozio è ancora chiuso;
  * gli id job stabili sono una trappola nota: arq rifiuta un id il cui job *o
    risultato* esiste ancora (`keep_result`, un'ora), quindi un id riusato fa
    sparire in silenzio ogni ripresa dopo la prima.

Lo stato sta invece in una colonna, che è la stessa scelta dei tre emettitori
edge-triggered già in casa (`no_answer_fired_for`, `dormant_fired_for`,
`handoff_sla_fired_for`): sopravvive ai riavvii, recupera i tick persi, e
rivaluta gli orari **freschi** a ogni passata.

**Il costo**: la risposta arriva entro cinque minuti dall'apertura, non
all'istante. Per un'attesa che è già durata una notte è un arrotondamento, e
per il cliente somiglia più a un negozio che apre e sbriga la posta che a un
robot che scatta alle 09:00:00.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ai_core import ConversationService
from ai_core.response_hours import resolve_response_hours
from db import (
    AnalyticsRepository,
    ConversationRepository,
    OffHoursPendingCandidate,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.repositories.message import MessageRepository
from shared import get_logger
from workers.outbound import is_within_24h
from workers.runtime import Runtime

logger = get_logger(__name__)

# Oltre questa età il marcatore viene lasciato cadere anche se il merchant non
# ha mai riaperto. Difende dal caso "agenda configurata male": senza, una
# conversazione resterebbe candidata a ogni passata per sempre, e col cap della
# scansione finirebbe per affamare le attese vere.
_MAX_PENDING_AGE_HOURS = 24 * 14


async def resume_after_hours(ctx: dict[str, Any]) -> dict[str, Any]:
    """Riprende le conversazioni sospese il cui merchant ha riaperto."""
    runtime: Runtime = ctx["runtime"]
    candidates = await _scan()
    logger.info("resume_after_hours.scan", count=len(candidates))

    now = datetime.now(tz=UTC)
    resumed = skipped_closed = skipped_human = expired = claimed_elsewhere = failed = 0

    for cand in candidates:
        try:
            outcome = await _resume_one(cand, runtime=runtime, now=now)
        except Exception as e:  # pragma: no cover — una riga non deve fermare lo sweep
            logger.warning(
                "resume_after_hours.candidate_failed",
                error=str(e),
                conversation_id=str(cand.conversation_id),
            )
            failed += 1
            continue
        match outcome:
            case "resumed":
                resumed += 1
            case "still_closed":
                skipped_closed += 1
            case "human_replied":
                skipped_human += 1
            case "expired" | "window_expired":
                expired += 1
            case "already_claimed":
                claimed_elsewhere += 1
            case _:
                failed += 1

    return {
        "candidates": len(candidates),
        "resumed": resumed,
        "still_closed": skipped_closed,
        "human_replied": skipped_human,
        "expired": expired,
        # Passata precedente ancora in corso su queste righe. Un valore
        # stabilmente alto significa che il giro non sta al passo del tick.
        "already_claimed": claimed_elsewhere,
        "failed": failed,
    }


async def _scan() -> list[OffHoursPendingCandidate]:
    async with session_scope() as session:
        return await ConversationRepository(session).list_off_hours_pending()


async def _resume_one(cand: OffHoursPendingCandidate, *, runtime: Runtime, now: datetime) -> str:
    tenant_ctx = TenantContext(
        tenant_id=cand.tenant_id,
        merchant_id=cand.merchant_id,
        role="worker",
        actor_id=cand.merchant_id,
    )

    async with tenant_session(tenant_ctx) as session:
        convs = ConversationRepository(session)

        # Marcatore troppo vecchio: l'agenda non riapre (o non riaprirà mai).
        age_hours = (now - cand.pending_since).total_seconds() / 3600
        if age_hours > _MAX_PENDING_AGE_HOURS:
            await convs.clear_off_hours_pending(cand.conversation_id)
            logger.info(
                "resume_after_hours.expired",
                conversation_id=str(cand.conversation_id),
                age_hours=round(age_hours, 1),
            )
            return "expired"

        # Siamo di nuovo dentro gli orari? Risolto adesso, non alla chiusura:
        # è il motivo per cui questo è uno sweep e non un job differito.
        hours = await resolve_response_hours(session, cand.merchant_id)
        if not hours.is_open(now):
            return "still_closed"

        # Ha già risposto un operatore? Allora il bot tace, e il marcatore va
        # tolto: la conversazione non è più in attesa di nessuno.
        #
        # I casi più comuni (risposta dal composer, takeover manuale) sono già
        # esclusi dalla scansione perché spengono `auto_reply` e aprono un
        # handoff. Questo controllo copre quello che resta, cioè il merchant
        # che ha risposto dall'app WhatsApp del telefono: quella strada mette
        # solo una pausa di due ore, che dopo una notte è scaduta da un pezzo.
        if await convs.human_replied_since(cand.conversation_id, cand.pending_since):
            await convs.clear_off_hours_pending(cand.conversation_id)
            logger.info(
                "resume_after_hours.human_replied",
                conversation_id=str(cand.conversation_id),
            )
            return "human_replied"

        pending = await MessageRepository(session).list_inbound_since(
            cand.conversation_id, cand.pending_since
        )
        if not pending:
            # Nessuna domanda da riprendere (messaggi cancellati, o marcatore
            # rimasto orfano): niente da dire, si pulisce.
            await convs.clear_off_hours_pending(cand.conversation_id)
            return "expired"

        # Finestra di servizio WhatsApp: oltre le 24 ore dall'ultimo messaggio
        # del cliente il testo libero è vietato dalla piattaforma e servirebbe
        # un template approvato. Succede sulle chiusure lunghe (venerdì sera →
        # lunedì mattina). Non inventiamo un template qui: si lascia cadere,
        # si registra un evento — così un'automazione della lavagnetta può
        # agganciarlo e decidere (template di ri-agancio, avviso all'operatore)
        # — e resta una domanda aperta di prodotto.
        last_inbound_at = pending[-1].created_at
        if last_inbound_at.tzinfo is None:
            last_inbound_at = last_inbound_at.replace(tzinfo=UTC)
        if not is_within_24h(last_inbound_at, now):
            await convs.clear_off_hours_pending(cand.conversation_id)
            await AnalyticsRepository(session).emit(
                tenant_id=cand.tenant_id,
                merchant_id=cand.merchant_id,
                event_type="conversation.resume_expired",
                subject_type="conversation",
                subject_id=cand.conversation_id,
                properties={
                    "conversation_id": str(cand.conversation_id),
                    "waited_hours": round(age_hours, 1),
                    "reason": "whatsapp_24h_window_closed",
                },
            )
            logger.info(
                "resume_after_hours.window_expired",
                conversation_id=str(cand.conversation_id),
                waited_hours=round(age_hours, 1),
            )
            return "window_expired"

        texts = [m.content for m in pending if m.content]
        wa_ids = [m.wa_message_id for m in pending if m.wa_message_id]

        # Ultimo passo prima di spendere una chiamata al modello: prendere in
        # carico la conversazione. Due passate dello sweep possono
        # sovrapporsi — 500 conversazioni per giro, una chiamata LLM ciascuna,
        # e il lunedì dopo un fine settimana il giro dura più dei cinque minuti
        # fra un tick e l'altro — e senza claim il cliente riceverebbe due
        # risposte alla riapertura invece di una.
        #
        # Il claim viene per ultimo di proposito: i controlli qui sopra sono
        # tutti a costo zero e terminano senza inviare nulla, quindi
        # prenotarli sarebbe solo un modo per lasciare in giro claim da
        # rilasciare.
        if not await convs.claim_off_hours_resume(cand.conversation_id, cand.pending_since):
            logger.info(
                "resume_after_hours.already_claimed",
                conversation_id=str(cand.conversation_id),
            )
            return "already_claimed"

    # Fuori dalla sessione tenant: `generate_and_send_reply` apre la propria.
    # È la STESSA funzione che usa il flush del debounce, quindi rivaluta da
    # sola su stato fresco tutti i gate (merchant spento, thread in takeover,
    # opt-out, pausa soft) e ora anche l'orario — se nel frattempo qualcosa è
    # cambiato, non risponde e il marcatore resta per il tick successivo.
    service: ConversationService = runtime.conversation_service
    result = await service.generate_and_send_reply(
        phone_number_id=cand.wa_phone_number_id,
        from_phone=cand.wa_contact_phone,
        text="\n".join(texts),
        wa_message_id=wa_ids[-1] if wa_ids else None,
        exclude_wa_message_ids=wa_ids,
        resumed_after_hours=True,
    )

    if not result.handled:
        # Il marcatore resta apposta: la prossima passata riproverà. Un invio
        # fallito non deve consumare l'attesa in silenzio. Il claim invece va
        # rilasciato subito, altrimenti il ritentativo resterebbe fermo fino
        # alla sua scadenza.
        logger.info(
            "resume_after_hours.not_handled",
            conversation_id=str(cand.conversation_id),
            reason=result.reason,
        )
        async with tenant_session(tenant_ctx) as session:
            await ConversationRepository(session).release_off_hours_resume(cand.conversation_id)
        return result.reason or "not_handled"

    async with tenant_session(tenant_ctx) as session:
        await ConversationRepository(session).clear_off_hours_pending(cand.conversation_id)
    logger.info(
        "resume_after_hours.resumed",
        conversation_id=str(cand.conversation_id),
        messages=len(texts),
        waited_hours=round(age_hours, 1),
    )
    return "resumed"
