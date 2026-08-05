"""UC-03 — no-answer trigger emitter.

ADR 0015: this scheduler is a pure *edge-triggered emitter*. Every 15 minutes it
scans for conversations that went silent, and emits a `lead.no_answer` analytics
event **once per silence episode**. It sends nothing itself — the response
(content + multi-attempt cadence) lives entirely in the merchant's automation on
the lavagnetta, dispatched by the automation engine off this event.

Idempotency is edge-triggered, not Redis-based: the emission is anchored on the
episode anchor (`no_answer_fired_for` vs `_episode_anchor`). We fire once and
suppress until the lead sends a new inbound (advancing `last_inbound_at`), which
re-arms the trigger for the next silence episode.

Il silenzio vale in due forme (ADR 0025), e per un pezzo ne contava una sola:
il lead che risponde e poi sparisce (ancora = `last_inbound_at`) **e** il lead
che a un primo contatto in uscita non risponde mai (ancora = `started_at`). Il
secondo era escluso da un `last_inbound_at IS NOT NULL` nella scansione, ed è il
caso più comune di tutti su un merchant che fa outreach: `lead.no_answer` non è
mai stato emesso in produzione fino a questo fix.

Vincolo di ordinamento con lo sweep di chiusura (`close_conversations.py`): la
scansione qui vede solo conversazioni `active`, quindi la chiusura per
inattività **deve** avvenire dopo il follow-up più lungo configurato. Le due
soglie erano indipendenti ed entrambe a 120 minuti, il che rendeva
irraggiungibile qualunque trigger con ritardo ≥ 120. Oggi il legame è esplicito:
entrambi gli sweep leggono i ritardi dallo stesso posto
(`AutomationRepository.enabled_trigger_delays`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from db import (
    AnalyticsRepository,
    AutomationRepository,
    ConversationRepository,
    ReminderCandidate,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.models.automation import AutomationFlow
from shared import get_logger

logger = get_logger(__name__)

# Ritardo attribuito a un nodo trigger che non ne dichiara uno (l'editor ne
# scrive sempre uno, ma un grafo importato o modificato a mano può non averlo).
DEFAULT_DELAY_MINUTES = 120

# Pavimento assoluto della scansione. Non è una regola di prodotto — è solo il
# valore minimo sotto il quale non ha senso scandire ogni 15 minuti, visto che
# il cron non può reagire più in fretta del proprio tick.
_SCAN_FLOOR_MINUTES = 5


async def followup_no_answer(ctx: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    delays = await _enabled_delays()
    if not delays:
        # Nessun merchant ha un'automazione "nessuna risposta" attiva: non c'è
        # niente da emettere, e la scansione della tabella conversations si può
        # saltare del tutto.
        return {"candidates": 0, "emitted": 0, "skipped": "no_enabled_automations"}

    # Il pavimento è il ritardo più corto davvero configurato sulla piattaforma:
    # ogni candidato viene poi ri-filtrato con la soglia del proprio merchant.
    floor = max(_SCAN_FLOOR_MINUTES, min(min(v) for v in delays.values()))
    candidates = await _scan_candidates(floor)
    logger.info("uc03.scan", count=len(candidates), floor_min=floor)

    emitted = 0
    for cand in candidates:
        if await _maybe_emit(cand, now=now):
            emitted += 1

    return {"candidates": len(candidates), "emitted": emitted}


async def _enabled_delays() -> dict[Any, list[int]]:
    async with session_scope() as session:
        return await AutomationRepository(session).enabled_trigger_delays(
            trigger_type="no_answer", default_minutes=DEFAULT_DELAY_MINUTES
        )


async def _scan_candidates(min_idle_minutes: int) -> list[ReminderCandidate]:
    async with session_scope() as session:
        repo = ConversationRepository(session)
        return await repo.list_reminder_candidates(min_idle_minutes=min_idle_minutes)


async def _maybe_emit(cand: ReminderCandidate, *, now: datetime) -> bool:
    # Edge gate: fire once per silence episode, keyed on the episode anchor.
    anchor = _episode_anchor(cand)
    if cand.no_answer_fired_for is not None and cand.no_answer_fired_for >= anchor:
        return False

    tenant_ctx = TenantContext(
        tenant_id=cand.tenant_id,
        merchant_id=cand.merchant_id,
        role="worker",
        actor_id=cand.merchant_id,
    )
    async with tenant_session(tenant_ctx) as session:
        autos = await AutomationRepository(session).list_enabled_by_trigger(
            merchant_id=cand.merchant_id, trigger_type="no_answer"
        )
        if not autos:
            # Nobody is listening — don't emit (and don't burn the anchor, so the
            # trigger fires the moment the merchant enables a no-answer automation).
            return False

        threshold_min = _threshold_minutes(autos)
        if now - cand.last_message_at < timedelta(minutes=threshold_min):
            return False

        await ConversationRepository(session).mark_no_answer_fired(cand.conversation_id, anchor)
        await AnalyticsRepository(session).emit(
            tenant_id=cand.tenant_id,
            merchant_id=cand.merchant_id,
            event_type="lead.no_answer",
            subject_type="conversation",
            subject_id=cand.conversation_id,
            properties={
                "idle_minutes": int((now - cand.last_message_at).total_seconds() / 60),
                # Re-engagement anchor: the engine cancels a stale cadence if the
                # lead's last_inbound_at advances past this at resume time.
                "episode_anchor": anchor.isoformat(),
                # Distingue i due silenzi nelle statistiche: chi non ha mai
                # risposto a un primo contatto è un caso diverso da chi ha
                # risposto e poi è sparito, e i merchant li leggono diversamente.
                "never_replied": cand.last_inbound_at is None,
            },
        )
        logger.info("uc03.emitted", conversation_id=str(cand.conversation_id))
    return True


def _episode_anchor(cand: ReminderCandidate) -> datetime:
    """L'istante che identifica questo episodio di silenzio (ADR 0025).

    `last_inbound_at` quando il lead ha parlato almeno una volta: l'episodio è il
    silenzio che segue la sua ultima parola, e un nuovo inbound lo chiude
    riarmando il trigger. Quando invece non ha MAI risposto si ripiega su
    `started_at`.

    Il ripiego deve essere un istante **immobile**, ed è il punto delicato di
    tutto il fix. L'alternativa ovvia — ancorare a `last_message_at` — si
    autoalimenta: il sollecito che l'automazione manda fa avanzare
    `last_message_at`, il che riarma il trigger, che dopo altri `delay_minutes`
    emette di nuovo, all'infinito e senza che il lead abbia fatto nulla.
    `started_at` non si muove mai (nemmeno alla riapertura di una conversazione
    chiusa), quindi il lead che non risponde riceve esattamente un sollecito.

    E il riarmo continua a funzionare: se un giorno risponde, `last_inbound_at`
    diventa più recente di `started_at` e supera l'ancora bruciata, quindi il
    silenzio successivo è un episodio nuovo.
    """
    return cand.last_inbound_at or cand.started_at


def _threshold_minutes(autos: list[AutomationFlow]) -> int:
    """Smallest `no_answer` trigger delay across the enabled automations — the
    trigger fires as soon as the earliest-configured one wants it. Falls back to
    `DEFAULT_DELAY_MINUTES` when no trigger sets an explicit `delay_minutes`."""
    values: list[int] = []
    for auto in autos:
        trigger = next((n for n in auto.nodes if n.kind == "trigger"), None)
        delay = (trigger.config or {}).get("delay_minutes") if trigger else None
        if isinstance(delay, (int, float)) and delay > 0:
            values.append(int(delay))
    return min(values) if values else DEFAULT_DELAY_MINUTES
