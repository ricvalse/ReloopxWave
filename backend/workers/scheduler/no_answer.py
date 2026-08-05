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
from uuid import UUID

from db import (
    ANCHOR_ANY,
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
        emitted += await _maybe_emit(cand, now=now)

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


async def _maybe_emit(cand: ReminderCandidate, *, now: datetime) -> int:
    """Emette un `lead.no_answer` **per automazione** che ha davvero titolo a
    partire su questa conversazione. Ritorna quante ne ha emesse.

    Era una valutazione sola per conversazione: soglia = il minimo dei ritardi
    del merchant, un'emissione, e il dispatch ventagliava su tutte le automazioni
    `no_answer`. Con una sola automazione funzionava; con due no, ed è
    esattamente lo scenario di ADR 0027 (una per template). Con ritardi 60 e 240,
    l'emissione avveniva a 60, l'ancora si bruciava lì, e quella da 240 non
    partiva mai — il suo ritardo era ignorato in silenzio.
    """
    anchor = _episode_anchor(cand)
    tenant_ctx = TenantContext(
        tenant_id=cand.tenant_id,
        merchant_id=cand.merchant_id,
        role="worker",
        actor_id=cand.merchant_id,
    )
    emitted = 0
    async with tenant_session(tenant_ctx) as session:
        autos = await AutomationRepository(session).list_enabled_by_trigger(
            merchant_id=cand.merchant_id, trigger_type="no_answer"
        )
        if not autos:
            # Nobody is listening — don't emit (and don't burn the anchor, so the
            # trigger fires the moment the merchant enables a no-answer automation).
            return 0

        idle = now - cand.last_message_at
        for auto in autos:
            if idle < timedelta(minutes=_delay_minutes(auto)):
                continue
            if not _source_matches(auto, cand):
                continue
            if _already_fired(cand, auto.id, anchor):
                continue

            await ConversationRepository(session).mark_no_answer_fired(
                cand.conversation_id, auto.id, anchor
            )
            await AnalyticsRepository(session).emit(
                tenant_id=cand.tenant_id,
                merchant_id=cand.merchant_id,
                event_type="lead.no_answer",
                subject_type="conversation",
                subject_id=cand.conversation_id,
                properties={
                    "idle_minutes": int(idle.total_seconds() / 60),
                    # Re-engagement anchor: the engine cancels a stale cadence if the
                    # lead's last_inbound_at advances past this at resume time.
                    "episode_anchor": anchor.isoformat(),
                    # Distingue i due silenzi nelle statistiche: chi non ha mai
                    # risposto a un primo contatto è un caso diverso da chi ha
                    # risposto e poi è sparito, e i merchant li leggono diversamente.
                    "never_replied": cand.last_inbound_at is None,
                    # L'evento è già indirizzato: la soglia e il filtro di
                    # provenienza sono stati valutati qui, quindi il dispatcher
                    # non deve ri-ventagliare su tutte le automazioni `no_answer`.
                    "target_automation_id": str(auto.id),
                    # Provenienza, per le statistiche e per leggere un evento
                    # senza dover risalire ai messaggi.
                    "source_template_id": (
                        str(cand.last_outbound_template_id)
                        if cand.last_outbound_template_id
                        else None
                    ),
                    "source_template_name": cand.last_outbound_template_name,
                },
            )
            emitted += 1
            logger.info(
                "uc03.emitted",
                conversation_id=str(cand.conversation_id),
                automation_id=str(auto.id),
            )
    return emitted


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


def _trigger_config(auto: AutomationFlow) -> dict[str, Any]:
    """La config del nodo trigger del grafo. È lì che l'editor scrive, non in
    `AutomationFlow.trigger_config` — che resta la copia usata dal dispatcher."""
    trigger = next((n for n in auto.nodes if n.kind == "trigger"), None)
    return (trigger.config if trigger else None) or {}


def _delay_minutes(auto: AutomationFlow) -> int:
    """Il ritardo di QUESTA automazione, non più il minimo del merchant."""
    delay = _trigger_config(auto).get("delay_minutes")
    if isinstance(delay, (int, float)) and delay > 0:
        return int(delay)
    return DEFAULT_DELAY_MINUTES


def _source_matches(auto: AutomationFlow, cand: ReminderCandidate) -> bool:
    """Il filtro di provenienza (ADR 0027): "non ha risposto **a questo**".

    `source_template_id` vuoto = nessun filtro, cioè il comportamento storico
    ("questa chat è ferma da X minuti", qualunque cosa sia stato l'ultimo
    messaggio). Valorizzato, l'automazione parte solo se l'ultimo messaggio in
    uscita è stato mandato con quel template — non su una risposta dell'AI, non
    su una frase scritta a mano da un operatore, non su un altro template.

    Il filtro sta qui e non in `_trigger_config_match` — dove vivono quelli dei
    trigger CRM — perché qui l'ancora viene bruciata: scartare a valle, nel
    dispatcher, timbrerebbe l'episodio per un'automazione che non doveva
    nemmeno essere considerata.
    """
    wanted = str(_trigger_config(auto).get("source_template_id") or "").strip()
    if not wanted:
        return True
    actual = str(cand.last_outbound_template_id or "")
    return actual == wanted


def _already_fired(cand: ReminderCandidate, automation_id: UUID, anchor: datetime) -> bool:
    """Questa automazione ha già emesso per questo episodio di silenzio?

    `ANCHOR_ANY` copre le ancore scritte nella forma vecchia, quando il timbro
    era uno solo per conversazione: valgono per qualunque automazione.
    """
    anchors = cand.no_answer_fired_for
    fired = anchors.get(str(automation_id)) or anchors.get(ANCHOR_ANY)
    return fired is not None and fired >= anchor
