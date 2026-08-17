"""UC-06 — dormant-lead trigger emitter.

ADR 0015: this scheduler is a pure *edge-triggered emitter*. Daily it scans for
leads whose most-recent conversation activity is older than the merchant's
configured dormancy threshold, and emits a `lead.dormant` analytics event **once
per dormancy episode**. It sends nothing — the reactivation message(s) and their
cadence live in the merchant's automation on the lavagnetta, dispatched by the
automation engine off this event.

Idempotency is edge-triggered: the emission is anchored on the lead's
`last_interaction_at` (`dormant_fired_for`). We fire once and re-arm only when the
lead re-engages (advancing their max conversation activity past the anchor).

La soglia è **quella che il merchant ha scritto sul nodo trigger**, e per un
pezzo non lo è stata: la scansione partiva da una costante di 30 giorni, quindi
un'automazione impostata a 2 giorni non poteva emettere nulla — la UI accetta il
valore, il backend lo ignora, e nessuno dei due lo dice. Oggi il pavimento si
deriva dalle soglie davvero configurate, come già faceva l'emettitore no-answer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from db import (
    AnalyticsRepository,
    AutomationRepository,
    LeadRepository,
    ReactivationCandidate,
    TenantContext,
    session_scope,
    tenant_session,
)
from db.models.automation import AutomationFlow
from shared import get_logger

logger = get_logger(__name__)

# Soglia attribuita a un nodo trigger che non dichiara `days` (l'editor ne scrive
# sempre uno, ma un grafo importato o modificato a mano può non averlo).
_DEFAULT_DORMANT_DAYS = 90

# Pavimento assoluto della scansione. Non è una regola di prodotto — è solo il
# valore minimo sotto il quale non ha senso scandire una volta al giorno, visto
# che il cron non può reagire più in fretta del proprio tick.
_SCAN_FLOOR_DAYS = 1


async def reactivate_dormant_leads(ctx: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    thresholds = await _enabled_thresholds()
    if not thresholds:
        # Nessun merchant ha un'automazione "lead dormiente" attiva: non c'è
        # niente da emettere, e la scansione si può saltare del tutto.
        return {"candidates": 0, "emitted": 0, "skipped": "no_enabled_automations"}

    # Il pavimento è la dormienza più breve davvero configurata sulla
    # piattaforma; ogni candidato viene poi ri-filtrato con la soglia del proprio
    # merchant in `_maybe_emit`.
    #
    # Era la costante `_MIN_DORMANT_DAYS = 30`, ed è lo stesso difetto già
    # corretto per il no-answer: la UI accetta "2 giorni", il backend scandisce
    # da 30 in su, e il merchant che ha scritto 2 non vede partire niente né
    # legge da nessuna parte il perché. Qualunque soglia sotto il mese era
    # irraggiungibile in silenzio.
    floor = max(_SCAN_FLOOR_DAYS, min(min(v) for v in thresholds.values()))
    candidates = await _scan_candidates(dormant_cutoff=now - timedelta(days=floor))
    logger.info("uc06.scan", count=len(candidates), floor_days=floor)

    emitted = 0
    for cand in candidates:
        if await _maybe_emit(cand, now=now):
            emitted += 1

    return {"candidates": len(candidates), "emitted": emitted}


async def _enabled_thresholds() -> dict[Any, list[int]]:
    async with session_scope() as session:
        return await AutomationRepository(session).enabled_trigger_thresholds(
            trigger_type="lead_dormant", config_key="days", default=_DEFAULT_DORMANT_DAYS
        )


async def _scan_candidates(*, dormant_cutoff: datetime) -> list[ReactivationCandidate]:
    async with session_scope() as session:
        return await LeadRepository(session).list_reactivation_candidates(
            dormant_cutoff=dormant_cutoff
        )


def _episode_anchor(cand: ReactivationCandidate) -> datetime:
    """L'istante che identifica questo episodio di dormienza, e deve essere
    **immobile** (stessa regola di `no_answer._episode_anchor`, ADR 0025).

    Era `last_interaction_at`, cioè il massimo `last_message_at` fra le
    conversazioni del lead — un istante che **i nostri stessi messaggi fanno
    avanzare**. Il sollecito partiva, spostava l'ancora, il trigger si riarmava,
    e dopo altri `days` ripartiva: un lead che non risponde mai riceveva un
    messaggio ogni `days` giorni per sempre. Con il default di 90 giorni si
    notava appena; con i 2 giorni che un merchant può configurare diventa un
    invio ogni due giorni a tutta la base dormiente.

    `last_inbound_at` quando il lead ha parlato almeno una volta: l'episodio è il
    silenzio che segue la sua ultima parola, e un nuovo inbound lo chiude
    riarmando il trigger. Quando invece non ha MAI risposto si ripiega sulla
    creazione del lead, che non si muove mai.

    La soglia resta invece su `last_interaction_at`: la dormienza *dura* dal
    nostro ultimo contatto, ed è giusto che il sollecito non parta il giorno dopo
    che gli abbiamo scritto. A muoversi è la misura, non l'identità dell'episodio.
    """
    return cand.last_inbound_at or cand.first_seen_at


async def _maybe_emit(cand: ReactivationCandidate, *, now: datetime) -> bool:
    # Edge gate: fire once per dormancy episode, keyed on the immobile anchor.
    anchor = _episode_anchor(cand)
    if cand.dormant_fired_for is not None and cand.dormant_fired_for >= anchor:
        return False

    tenant_ctx = TenantContext(
        tenant_id=cand.tenant_id,
        merchant_id=cand.merchant_id,
        role="worker",
        actor_id=cand.merchant_id,
    )
    async with tenant_session(tenant_ctx) as session:
        autos = await AutomationRepository(session).list_enabled_by_trigger(
            merchant_id=cand.merchant_id, trigger_type="lead_dormant"
        )
        if not autos:
            return False

        days = _threshold_days(autos)
        if now - cand.last_interaction_at < timedelta(days=days):
            return False

        await LeadRepository(session).mark_dormant_fired(cand.lead_id, anchor)
        await AnalyticsRepository(session).emit(
            tenant_id=cand.tenant_id,
            merchant_id=cand.merchant_id,
            event_type="lead.dormant",
            subject_type="lead",
            subject_id=cand.lead_id,
            properties={
                "days_dormant": int((now - cand.last_interaction_at).total_seconds() / 86400),
                # Re-engagement anchor for the engine's stale-cadence guard: lo
                # stesso istante immobile su cui è timbrata l'idempotenza.
                "episode_anchor": anchor.isoformat(),
                # Distingue i due silenzi, come fa il no-answer: chi non ha mai
                # risposto è un caso diverso da chi ha risposto e poi è sparito.
                "never_replied": cand.last_inbound_at is None,
            },
        )
        logger.info("uc06.emitted", lead_id=str(cand.lead_id))
    return True


def _threshold_days(autos: list[AutomationFlow]) -> int:
    """Smallest `lead_dormant` trigger threshold (days) across the enabled
    automations. Falls back to the config-era default when none set it."""
    values: list[int] = []
    for auto in autos:
        trigger = next((n for n in auto.nodes if n.kind == "trigger"), None)
        days = (trigger.config or {}).get("days") if trigger else None
        if isinstance(days, (int, float)) and days > 0:
            values.append(int(days))
    return min(values) if values else _DEFAULT_DORMANT_DAYS
