"""Catalogo tipato degli `analytics_events` — single source of truth.

Ogni evento scritto in `analytics_events` (via `AnalyticsRepository.emit` o
costruzione diretta di `AnalyticsEvent`) deve avere il suo `event_type` qui.

Perché esiste questo modulo (ADR 0021):
- gli event_type erano stringhe libere sparse su ~25 call-site e lette per
  stringa altrove. Una divergenza silenziosa fra emitter e reader ha prodotto un
  bug reale: le KPI contavano ``reminder.sent`` mentre lo scheduler emetteva
  ``appointment_reminder.sent`` → la metrica era sempre 0.
- centralizzare qui i nomi (con label/descrizione/categoria) dà: un vocabolario
  per la dashboard configurabile (Asse 1), un fix per-costruzione del bug sopra
  (i reader referenziano l'enum, non una stringa), e un punto unico da tenere
  sincronizzato (garantito dal test ``test_analytics_events`` che asserisce che
  ogni ``event_type=`` letterale nel sorgente ∈ :class:`EventType`).

NON è la definizione di *quali metriche* mostra un merchant (quella vivrà in una
chiave tipata del config cascade): è solo l'elenco degli eventi che il sistema
sa emettere, con i metadati per etichettarli.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EventType(StrEnum):
    """Tutti gli `event_type` che il backend sa emettere su `analytics_events`.

    Il *valore* è la stringa scritta a DB (immutabile: cambiarla rompe lo
    storico append-only). Usare questi membri nei reader (KPI/query) invece di
    stringhe libere, così un typo diventa un errore statico e non una metrica a 0.
    """

    # -- Conversazione / AI ------------------------------------------------
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_REPLIED = "message.replied"
    LEAD_SCORE_CHANGED = "lead_score_changed"
    LEAD_OPTED_OUT = "lead.opted_out"

    # -- Booking / appuntamenti -------------------------------------------
    BOOKING_CREATED = "booking.created"
    BOOKING_FAILED = "booking.failed"
    BOOKING_RESCHEDULED = "booking.rescheduled"
    BOOKING_CANCELLED = "booking.cancelled"
    APPOINTMENT_REMINDER_SENT = "appointment_reminder.sent"

    # -- Pipeline / CRM ----------------------------------------------------
    PIPELINE_MOVED = "pipeline.moved"
    PIPELINE_FAILED = "pipeline.failed"
    LEAD_CRM_CREATED = "lead.crm_created"
    OPPORTUNITY_CREATED = "opportunity.created"

    # -- Scheduler / lifecycle lead ---------------------------------------
    LEAD_NO_ANSWER = "lead.no_answer"
    LEAD_DORMANT = "lead.dormant"

    # -- Escalation / handoff ---------------------------------------------
    CONVERSATION_ESCALATED = "conversation.escalated"
    CONVERSATION_HANDOFF_OVERDUE = "conversation.handoff_overdue"
    ESCALATION_RISK_HIGH = "escalation.risk_high"

    # -- Obiezioni ---------------------------------------------------------
    OBJECTIONS_CLASSIFIED = "objections.classified"

    # -- Privacy / retention ----------------------------------------------
    RETENTION_PURGED = "retention.purged"

    # -- Sistema / operazioni ---------------------------------------------
    KB_REINDEXED = "kb.reindexed"
    KB_REINDEX_FAILED = "kb.reindex_failed"

    # -- Rollup sintetici (scritti dal cron kpi_rollup nella stessa tabella)
    KPI_DAILY_MESSAGES_RECEIVED = "kpi.daily.messages_received"
    KPI_DAILY_CONVERSATIONS = "kpi.daily.conversations"
    KPI_DAILY_HOT_LEADS = "kpi.daily.hot_leads"


class EventCategory(StrEnum):
    """Raggruppamento degli eventi per la UI del catalogo/metriche."""

    CONVERSATION = "conversation"
    BOOKING = "booking"
    PIPELINE = "pipeline"
    LEAD = "lead"
    ESCALATION = "escalation"
    OBJECTIONS = "objections"
    PRIVACY = "privacy"
    SYSTEM = "system"
    ROLLUP = "rollup"


@dataclass(slots=True, frozen=True)
class EventTypeDef:
    """Metadati di un event_type per la dashboard configurabile."""

    event_type: EventType
    label: str
    description: str
    category: EventCategory
    subject_type: str | None
    # `selectable`=False → evento operativo/sintetico che NON va offerto come
    # metrica scegliibile dal merchant (rollup interni, log di sistema).
    selectable: bool = True


# Catalogo completo. Il test `test_event_catalog_covers_every_event_type`
# garantisce che ogni membro di EventType abbia esattamente una voce qui.
EVENT_CATALOG: dict[EventType, EventTypeDef] = {
    d.event_type: d
    for d in (
        # -- Conversazione / AI --
        EventTypeDef(
            EventType.MESSAGE_RECEIVED,
            "Messaggi ricevuti",
            "Messaggi in ingresso dai lead presi in carico dall'agente.",
            EventCategory.CONVERSATION,
            "conversation",
        ),
        EventTypeDef(
            EventType.MESSAGE_REPLIED,
            "Risposte AI inviate",
            "Messaggi in uscita generati dall'agente in risposta a un lead.",
            EventCategory.CONVERSATION,
            "conversation",
        ),
        EventTypeDef(
            EventType.LEAD_SCORE_CHANGED,
            "Variazioni di score",
            "Aggiornamenti del punteggio di qualificazione del lead.",
            EventCategory.LEAD,
            "lead",
        ),
        EventTypeDef(
            EventType.LEAD_OPTED_OUT,
            "Opt-out",
            "Lead che hanno chiesto di non essere più contattati.",
            EventCategory.LEAD,
            "lead",
        ),
        # -- Booking --
        EventTypeDef(
            EventType.BOOKING_CREATED,
            "Appuntamenti presi",
            "Appuntamenti fissati dall'agente (include i soli-locali: usare "
            "properties.reason='booked' per i soli confermati su GHL).",
            EventCategory.BOOKING,
            "lead",
        ),
        EventTypeDef(
            EventType.BOOKING_FAILED,
            "Prenotazioni fallite",
            "Tentativi di prenotazione non andati a buon fine.",
            EventCategory.BOOKING,
            "lead",
        ),
        EventTypeDef(
            EventType.BOOKING_RESCHEDULED,
            "Appuntamenti riprogrammati",
            "Appuntamenti spostati a un nuovo orario.",
            EventCategory.BOOKING,
            "appointment",
        ),
        EventTypeDef(
            EventType.BOOKING_CANCELLED,
            "Appuntamenti cancellati",
            "Appuntamenti annullati.",
            EventCategory.BOOKING,
            "appointment",
        ),
        EventTypeDef(
            EventType.APPOINTMENT_REMINDER_SENT,
            "Promemoria inviati",
            "Promemoria di appuntamento inviati al lead.",
            EventCategory.BOOKING,
            "appointment",
        ),
        # -- Pipeline / CRM --
        EventTypeDef(
            EventType.PIPELINE_MOVED,
            "Spostamenti in pipeline",
            "Volte in cui un lead è stato spostato di stage/pipeline nel CRM.",
            EventCategory.PIPELINE,
            "lead",
        ),
        EventTypeDef(
            EventType.PIPELINE_FAILED,
            "Spostamenti pipeline falliti",
            "Tentativi di spostamento in pipeline non riusciti.",
            EventCategory.PIPELINE,
            "lead",
        ),
        EventTypeDef(
            EventType.LEAD_CRM_CREATED,
            "Lead creati da CRM",
            "Lead nati da un webhook GHL (ADR 0016).",
            EventCategory.PIPELINE,
            "lead",
        ),
        EventTypeDef(
            EventType.OPPORTUNITY_CREATED,
            "Opportunità create",
            "Opportunità GHL create per un lead.",
            EventCategory.PIPELINE,
            "lead",
        ),
        # -- Scheduler / lifecycle --
        EventTypeDef(
            EventType.LEAD_NO_ANSWER,
            "Lead senza risposta",
            "Lead rimasti in silenzio oltre la soglia di follow-up.",
            EventCategory.LEAD,
            "lead",
        ),
        EventTypeDef(
            EventType.LEAD_DORMANT,
            "Lead dormienti",
            "Lead che hanno superato la soglia di dormienza (riattivazione).",
            EventCategory.LEAD,
            "lead",
        ),
        # -- Escalation / handoff --
        EventTypeDef(
            EventType.CONVERSATION_ESCALATED,
            "Handoff a operatore",
            "Presa in carico da un umano (escalation o media non supportati).",
            EventCategory.ESCALATION,
            "conversation",
        ),
        EventTypeDef(
            EventType.CONVERSATION_HANDOFF_OVERDUE,
            "Handoff oltre SLA",
            "Handoff aperti che hanno superato lo SLA configurato.",
            EventCategory.ESCALATION,
            "conversation",
        ),
        EventTypeDef(
            EventType.ESCALATION_RISK_HIGH,
            "Conversazioni a rischio",
            "Turni con rischio di escalation elevato rilevato.",
            EventCategory.ESCALATION,
            "conversation",
        ),
        # -- Obiezioni --
        EventTypeDef(
            EventType.OBJECTIONS_CLASSIFIED,
            "Obiezioni classificate",
            "Obiezioni estratte e categorizzate dalle conversazioni.",
            EventCategory.OBJECTIONS,
            "conversation",
        ),
        # -- Privacy --
        EventTypeDef(
            EventType.RETENTION_PURGED,
            "Dati eliminati (retention)",
            "Dati purgati dal cron di retention.",
            EventCategory.PRIVACY,
            "merchant",
        ),
        # -- Sistema (non selezionabili come metrica di business) --
        EventTypeDef(
            EventType.KB_REINDEXED,
            "Reindex knowledge base",
            "Reindicizzazione della knowledge base completata.",
            EventCategory.SYSTEM,
            "merchant",
            selectable=False,
        ),
        EventTypeDef(
            EventType.KB_REINDEX_FAILED,
            "Reindex KB fallito",
            "Reindicizzazione della knowledge base non riuscita.",
            EventCategory.SYSTEM,
            "merchant",
            selectable=False,
        ),
        # -- Rollup sintetici (aggregati interni, non offerti come metrica) --
        EventTypeDef(
            EventType.KPI_DAILY_MESSAGES_RECEIVED,
            "Rollup: messaggi/giorno",
            "Aggregato giornaliero interno (cron kpi_rollup).",
            EventCategory.ROLLUP,
            "merchant",
            selectable=False,
        ),
        EventTypeDef(
            EventType.KPI_DAILY_CONVERSATIONS,
            "Rollup: conversazioni/giorno",
            "Aggregato giornaliero interno (cron kpi_rollup).",
            EventCategory.ROLLUP,
            "merchant",
            selectable=False,
        ),
        EventTypeDef(
            EventType.KPI_DAILY_HOT_LEADS,
            "Rollup: hot lead/giorno",
            "Aggregato giornaliero interno (cron kpi_rollup).",
            EventCategory.ROLLUP,
            "merchant",
            selectable=False,
        ),
    )
}


def event_catalog(*, selectable_only: bool = False) -> list[EventTypeDef]:
    """Ritorna il catalogo eventi come lista ordinata (per il ``/event-catalog``).

    Con ``selectable_only=True`` esclude gli eventi operativi/sintetici
    (rollup, log di sistema) che non ha senso offrire come metrica di business.
    """
    defs: list[EventTypeDef] = list(EVENT_CATALOG.values())
    if selectable_only:
        defs = [d for d in defs if d.selectable]
    return sorted(defs, key=lambda d: (d.category.value, d.event_type.value))
