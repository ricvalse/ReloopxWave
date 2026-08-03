"""Handoff SLA sweep — emit `conversation.handoff_overdue` for stale handoffs.

A human handoff (`auto_reply` off, `handoff_at` set, `handoff_resolved_at` null)
that stays open past `handoff_sla_minutes` gets a `conversation.handoff_overdue`
analytics event, **once per handoff episode** (edge-triggered on `handoff_at`,
mirroring the no-answer emitter — ADR 0015). That event drives any automation
subscribed to the `conversation_handoff_overdue` trigger, e.g. a Slack alert.

The sweep sends nothing itself and, like `followup_no_answer`, only fires when a
merchant actually has an enabled automation listening — otherwise it leaves the
anchor untouched so the trigger fires the moment they enable one.

La soglia è `escalation.sla_minutes`, **per merchant**. Era una variabile
d'ambiente globale (`settings.handoff_sla_minutes`): un unico valore per l'intera
piattaforma, che nessun merchant poteva adattare al proprio presidio. La
scansione usa il minimo consentito dallo schema come pavimento grezzo e poi
rifiltra ogni candidato con la soglia del suo merchant — gli handoff aperti sono
pochi, quindi allargare la scansione non costa nulla.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from config_resolver import ConfigKey, ConfigResolver
from db import (
    AnalyticsRepository,
    AutomationRepository,
    ConversationRepository,
    HandoffOverdueCandidate,
    TenantContext,
    session_scope,
    tenant_session,
)
from shared import get_logger

logger = get_logger(__name__)

# Pavimento della scansione: il minimo che lo schema consente per
# `escalation.sla_minutes`. Non è una soglia di prodotto — è solo il punto sotto
# il quale nessun merchant può configurarsi, quindi nulla di più stretto va letto.
_SCAN_FLOOR_MINUTES = 1

# Usato solo se la configurazione risolve a qualcosa di inutilizzabile. Un valore
# non positivo spingerebbe il cutoff nel futuro e farebbe risultare scaduto ogni
# handoff aperto: la guardia resta anche se lo schema già lo vieta in scrittura.
_DEFAULT_SLA_MINUTES = 15


def _as_positive_int(value: Any, *, default: int) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return default
    return minutes if minutes > 0 else default


async def handoff_sla_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    candidates = await _scan(_SCAN_FLOOR_MINUTES)
    logger.info("handoff_sla.scan", count=len(candidates))

    now = datetime.now(tz=UTC)
    emitted = 0
    for cand in candidates:
        if await _maybe_emit(cand, now=now):
            emitted += 1

    return {"candidates": len(candidates), "emitted": emitted}


async def _scan(min_overdue_minutes: int) -> list[HandoffOverdueCandidate]:
    async with session_scope() as session:
        return await ConversationRepository(session).list_overdue_handoffs(
            min_overdue_minutes=min_overdue_minutes
        )


async def _maybe_emit(cand: HandoffOverdueCandidate, *, now: datetime) -> bool:
    # Edge gate: fire once per handoff episode, keyed on handoff_at.
    if cand.sla_fired_for is not None and cand.sla_fired_for >= cand.handoff_at:
        return False

    tenant_ctx = TenantContext(
        tenant_id=cand.tenant_id,
        merchant_id=cand.merchant_id,
        role="worker",
        actor_id=cand.merchant_id,
    )
    async with tenant_session(tenant_ctx) as session:
        autos = await AutomationRepository(session).list_enabled_by_trigger(
            merchant_id=cand.merchant_id, trigger_type="conversation_handoff_overdue"
        )
        if not autos:
            # Nobody listening — don't emit and don't burn the anchor, so the
            # trigger fires the moment the merchant enables an overdue automation.
            return False

        # Soglia del merchant, non della piattaforma. Risolta qui dentro perché
        # è l'unico punto in cui abbiamo già una sessione tenant aperta, e solo
        # per i candidati che hanno davvero qualcuno in ascolto.
        threshold = _as_positive_int(
            await ConfigResolver(session).resolve(
                ConfigKey.ESCALATION_SLA_MINUTES, merchant_id=cand.merchant_id
            ),
            default=_DEFAULT_SLA_MINUTES,
        )
        if now - cand.handoff_at < timedelta(minutes=threshold):
            return False

        await ConversationRepository(session).mark_handoff_sla_fired(
            cand.conversation_id, cand.handoff_at
        )
        await AnalyticsRepository(session).emit(
            tenant_id=cand.tenant_id,
            merchant_id=cand.merchant_id,
            event_type="conversation.handoff_overdue",
            subject_type="conversation",
            subject_id=cand.conversation_id,
            properties={
                "lead_id": str(cand.lead_id) if cand.lead_id else None,
                "conversation_id": str(cand.conversation_id),
                "overdue_minutes": int((now - cand.handoff_at).total_seconds() / 60),
            },
        )
        logger.info("handoff_sla.emitted", conversation_id=str(cand.conversation_id))
    return True
