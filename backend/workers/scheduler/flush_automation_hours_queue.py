"""Svuota alla riapertura la coda delle automazioni fermate fuori orario.

Il gemello di `resume_after_hours`, per l'altro verso del traffico. Là si
riprende una domanda del cliente rimasta senza risposta; qui si riprende un
messaggio che il sistema doveva mandare e non poteva (ADR 0030).

Quando `schedule.apply_to_automations` è acceso e il merchant è chiuso, il walk
si ferma sul nodo customer-facing e lascia una riga in `automation_hours_queue`.
Questo sweep, ogni cinque minuti, cerca le righe il cui merchant è tornato
dentro i propri orari e **riaccoda** `automation_run` con `start_keys` uguali ai
nodi sospesi: il ramo riprende esattamente da lì.

**Perché riaccodare il run e non spedire da qui.** La riga in coda è un
puntatore, non un messaggio: non contiene testo. Rimandare il puntatore invece
del payload fa cadere gratis tre cose giuste alla ripresa —

  * il testo è quello **attuale** del nodo, quindi un nodo corretto durante la
    chiusura parte nella sua versione nuova (ADR 0014: il contenuto viene solo
    dalla lavagnetta, non da una sua copia invecchiata);
  * la finestra di servizio WhatsApp viene rivalutata su stato fresco, quindi
    `decide_outbound` sceglie testo libero o template secondo com'è *adesso*;
  * la guardia d'episodio di ADR 0015 riparte in `automation_run`, quindi una
    cadenza il cui lead ha risposto durante la notte si spegne da sola invece di
    insistere.

Rigenerare significa anche che un `ai_reply` sospeso non ha bruciato una
chiamata al modello per un messaggio mai spedito.

**Perché uno sweep e non un job differito**: le tre ragioni di ADR 0028 §2, qui
quasi identiche. L'attesa dura da una notte a un fine settimana lungo e in Redis
non sopravvive a un riavvio; il momento dell'apertura **cambia** (il merchant
corregge gli orari alle 08:00 e la coda deve partire alle 09:00, non all'orario
che avevamo calcolato alle 03:00); gli id job stabili sono la trappola nota di
arq.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ai_core.response_hours import resolve_response_hours
from db import (
    AnalyticsRepository,
    AutomationHoursQueueRepository,
    QueuedAutomationRun,
    TenantContext,
    session_scope,
    tenant_session,
)
from shared import get_logger

logger = get_logger(__name__)

# Oltre questa età la riga viene lasciata cadere anche se il merchant non ha mai
# riaperto. Stessa difesa (e stesso valore) di `resume_after_hours`: senza, una
# coda con un'agenda configurata male resterebbe candidata a ogni passata per
# sempre e, col cap della scansione, affamerebbe le attese vere.
_MAX_PENDING_AGE_HOURS = 24 * 14


async def flush_automation_hours_queue(ctx: dict[str, Any]) -> dict[str, Any]:
    """Riaccoda i rami di automazione il cui merchant ha riaperto."""
    redis = ctx["redis"]
    candidates = await _scan()
    logger.info("automation_hours_queue.scan", count=len(candidates))

    now = datetime.now(tz=UTC)
    resumed = still_closed = expired = already_claimed = failed = 0

    for cand in candidates:
        try:
            outcome = await _flush_one(cand, redis=redis, now=now)
        except Exception as e:  # pragma: no cover — una riga non ferma lo sweep
            logger.warning(
                "automation_hours_queue.candidate_failed",
                error=str(e),
                queue_id=str(cand.id),
            )
            failed += 1
            continue
        match outcome:
            case "resumed":
                resumed += 1
            case "still_closed":
                still_closed += 1
            case "expired":
                expired += 1
            case "already_claimed":
                already_claimed += 1
            case _:
                failed += 1

    return {
        "candidates": len(candidates),
        "resumed": resumed,
        "still_closed": still_closed,
        "expired": expired,
        # Passata precedente ancora in corso su queste righe. Un valore
        # stabilmente alto significa che il giro non sta al passo del tick.
        "already_claimed": already_claimed,
        "failed": failed,
    }


async def _scan() -> list[QueuedAutomationRun]:
    async with session_scope() as session:
        return await AutomationHoursQueueRepository(session).list_pending()


async def _flush_one(cand: QueuedAutomationRun, *, redis: Any, now: datetime) -> str:
    tenant_ctx = TenantContext(
        tenant_id=cand.tenant_id,
        merchant_id=cand.merchant_id,
        role="worker",
        actor_id=cand.merchant_id,
    )

    queued_at = cand.queued_at if cand.queued_at.tzinfo else cand.queued_at.replace(tzinfo=UTC)
    age_hours = (now - queued_at).total_seconds() / 3600

    async with tenant_session(tenant_ctx) as session:
        queue = AutomationHoursQueueRepository(session)

        # Riga troppo vecchia: l'agenda non riapre (o non riaprirà mai). Si
        # registra la caduta — un rinvio che scade in silenzio è
        # indistinguibile da un invio riuscito.
        if age_hours > _MAX_PENDING_AGE_HOURS:
            await queue.delete(cand.id)
            await AnalyticsRepository(session).emit(
                tenant_id=cand.tenant_id,
                merchant_id=cand.merchant_id,
                event_type="automation.send_dropped",
                subject_type=cand.subject_type or "lead",
                subject_id=cand.subject_id,
                # Colonna e non chiave di `properties`: è la dimensione su cui
                # la pagina Statistiche affetta (ADR 0021/0023).
                automation_id=cand.automation_id,
                properties={
                    "nodes": list(cand.node_keys),
                    "waited_hours": round(age_hours, 1),
                    "reason": "never_reopened",
                },
            )
            logger.info(
                "automation_hours_queue.expired",
                queue_id=str(cand.id),
                age_hours=round(age_hours, 1),
            )
            return "expired"

        # Siamo di nuovo dentro gli orari? Risolto adesso, non alla chiusura: è
        # il motivo per cui questo è uno sweep. E si rilegge anche
        # `apply_to_automations` — se il merchant l'ha spento durante la
        # chiusura, la coda va consegnata, non trattenuta per sempre.
        hours = await resolve_response_hours(session, cand.merchant_id)
        if hours.apply_to_automations and not hours.is_open(now):
            return "still_closed"

        # Il claim viene per ultimo: i controlli qui sopra sono a costo zero e
        # terminano senza fare nulla, quindi prenotarli sarebbe solo un modo per
        # lasciare in giro claim da rilasciare.
        if not await queue.claim(cand.id):
            logger.info("automation_hours_queue.already_claimed", queue_id=str(cand.id))
            return "already_claimed"

    # Fuori dalla sessione tenant. `automation_run` rivaluta da solo, su stato
    # fresco, tutto ciò che conta: flusso ancora abilitato, contesto risolvibile,
    # episodio non concluso (ADR 0015), takeover, finestra 24h — e gli orari, che
    # nel frattempo potrebbero essersi richiusi.
    try:
        await redis.enqueue_job(
            "automation_run",
            automation_id=str(cand.automation_id),
            tenant_id=str(cand.tenant_id),
            merchant_id=str(cand.merchant_id),
            subject_type=cand.subject_type,
            subject_id=str(cand.subject_id),
            start_keys=list(cand.node_keys),
            # Derivata dall'**id della riga**, non dalla sua `dedup_key`: la
            # chiave di coda è stabile per (automazione, soggetto, nodi), quindi
            # riusarla farebbe scartare in silenzio come "duplicate" la ripresa
            # della settimana dopo (la dedup del run vive 24h). L'id invece è
            # nuovo a ogni accodamento e stabile fra i ritentativi della stessa
            # riga — che è esattamente la dedup che serve.
            dedup=f"offhours-resume:{cand.id}",
            episode_anchor=cand.episode_anchor,
        )
    except Exception:
        # Il claim va rilasciato subito, altrimenti il ritentativo resterebbe
        # fermo fino alla sua scadenza. La riga resta in coda apposta.
        async with tenant_session(tenant_ctx) as session:
            await AutomationHoursQueueRepository(session).release(cand.id)
        raise

    async with tenant_session(tenant_ctx) as session:
        await AutomationHoursQueueRepository(session).delete(cand.id)
    logger.info(
        "automation_hours_queue.resumed",
        queue_id=str(cand.id),
        automation_id=str(cand.automation_id),
        nodes=list(cand.node_keys),
        waited_hours=round(age_hours, 1),
    )
    return "resumed"
