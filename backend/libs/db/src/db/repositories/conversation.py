from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    DateTime,
    cast,
    delete,
    exists,
    func,
    or_,
    select,
    text,
    true,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Conversation,
    ConversationProfile,
    Lead,
    Merchant,
    Message,
    WhatsAppTemplate,
)


def _open_handoff() -> ColumnElement[bool]:
    """SQL predicate for "a human owns this thread": handed off and not resolved.

    The single source of truth for the handoff-open test, mirrored by
    `ai_paused` in the automation engine. Anything that speaks to the customer
    on its own initiative must exclude these rows.
    """
    return Conversation.handoff_at.is_not(None) & Conversation.handoff_resolved_at.is_(None)


def _bot_owns_thread() -> ColumnElement[bool]:
    """Inverse of `_open_handoff()` — safe to send proactively."""
    return ~_open_handoff()


def _parse_iso(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp stored as text in JSONB meta (None on miss)."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# Chiave jolly nella mappa delle ancore: vale per ogni automazione. Serve solo a
# leggere la forma vecchia di `meta.no_answer_fired_for`, che era un singolo
# timestamp per conversazione invece che una mappa per automazione (ADR 0029).
ANCHOR_ANY = "*"


def _parse_anchor_map(raw: object) -> dict[str, datetime]:
    """Legge `meta.no_answer_fired_for` in entrambe le forme.

    Oggi è una mappa `{automation_id: iso}` — un'ancora per automazione, perché
    due automazioni "nessuna risposta" con ritardi diversi sono due episodi
    distinti e non possono condividere un timbro solo. Prima era un singolo
    timestamp: lo si legge come jolly, valido per qualunque automazione.
    """
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except ValueError:
        # Non è JSON valido: è la forma vecchia, un ISO nudo dentro il JSONB.
        ts = _parse_iso(raw)
        return {ANCHOR_ANY: ts} if ts else {}
    if isinstance(decoded, str):  # stringa JSON = forma vecchia
        ts = _parse_iso(decoded)
        return {ANCHOR_ANY: ts} if ts else {}
    if not isinstance(decoded, dict):
        return {}
    out: dict[str, datetime] = {}
    for key, value in decoded.items():
        ts = _parse_iso(value)
        if ts is not None:
            out[str(key)] = ts
    return out


@dataclass(slots=True, frozen=True)
class ReminderCandidate:
    conversation_id: UUID
    merchant_id: UUID
    tenant_id: UUID
    wa_phone_number_id: str
    wa_contact_phone: str
    last_message_at: datetime
    # Apertura della conversazione. È l'ancora di ripiego quando il lead non ha
    # MAI risposto (ADR 0025): `last_inbound_at` è NULL, ma `started_at` è
    # NOT NULL e — cosa che conta — non si muove mai, nemmeno quando il sollecito
    # che stiamo per mandare fa avanzare `last_message_at`. È quello che rende
    # l'emissione one-shot anche senza inbound.
    started_at: datetime
    # Last inbound (customer) message — drives the 24h window decision AND the
    # no-answer episode anchor (ADR 0015): the trigger fires once per silence
    # episode, re-arming only when the lead sends a new inbound. None quando il
    # lead non ha mai risposto (caso normale su un primo contatto in uscita), e
    # su righe legacy anteriori alla migrazione 0014.
    last_inbound_at: datetime | None = None
    # S-05: preferred send hour (0-23) learned by the send-time optimizer.
    optimal_send_hour: int | None = None
    # Lead display name — feeds `{name}` / `{{contact.name}}` in send-node free text.
    lead_name: str | None = None
    # ADR 0015/0029 edge-trigger anchors, una per automazione: `{automation_id:
    # ancora}`. Mappa vuota = mai emesso per questa conversazione. La chiave
    # `ANCHOR_ANY` è la forma vecchia (un'ancora sola per conversazione).
    no_answer_fired_for: dict[str, datetime] = field(default_factory=dict)
    # Provenienza dell'ultimo messaggio in uscita (ADR 0029): è ciò che permette
    # a un'automazione di dire "sollecita solo chi non ha risposto a QUESTO
    # template". `None` quando l'ultimo outbound non era un template — una
    # risposta dell'AI, o una frase scritta a mano da un operatore.
    last_outbound_template_id: UUID | None = None
    last_outbound_template_name: str | None = None


@dataclass(slots=True, frozen=True)
class HandoffOverdueCandidate:
    """A conversation whose human handoff has been open past the SLA.

    Edge-triggered like the no-answer emitter: `sla_fired_for` is the `handoff_at`
    we last emitted `conversation.handoff_overdue` for, so a re-escalation (a new
    `handoff_at`) re-arms the alert while an already-alerted one stays quiet.
    """

    conversation_id: UUID
    merchant_id: UUID
    tenant_id: UUID
    lead_id: UUID | None
    handoff_at: datetime
    sla_fired_for: datetime | None


@dataclass(slots=True, frozen=True)
class OffHoursPendingCandidate:
    """Una conversazione a cui il bot non ha risposto perché era fuori orario.

    Porta con sé tutto ciò che serve a rigenerare il turno senza un nuovo
    webhook WhatsApp: i due identificativi del canale e l'istante da cui
    ricostruire "che cosa è rimasto senza risposta".
    """

    conversation_id: UUID
    merchant_id: UUID
    tenant_id: UUID
    wa_phone_number_id: str
    wa_contact_phone: str
    pending_since: datetime


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(
        self,
        *,
        merchant_id: UUID,
        wa_contact_phone: str,
    ) -> Conversation | None:
        stmt = (
            select(Conversation)
            .where(
                Conversation.merchant_id == merchant_id,
                Conversation.wa_contact_phone == wa_contact_phone,
                Conversation.status == "active",
            )
            .order_by(Conversation.started_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_active_or_reopen_latest(
        self,
        *,
        merchant_id: UUID,
        wa_contact_phone: str,
    ) -> Conversation | None:
        """Returns the active conversation for this phone number, or reopens the
        most recent closed one. Returns None only if no conversation exists at all.

        Called on inbound messages and phone echoes so a new message from a known
        contact continues the existing thread rather than opening a duplicate.
        """
        stmt = (
            select(Conversation)
            .where(
                Conversation.merchant_id == merchant_id,
                Conversation.wa_contact_phone == wa_contact_phone,
            )
            .order_by(Conversation.started_at.desc())
            .limit(1)
        )
        conv = (await self._session.execute(stmt)).scalar_one_or_none()
        if conv is not None and conv.status != "active":
            conv.status = "active"
            await self._session.flush()
        return conv

    async def create(
        self,
        *,
        merchant_id: UUID,
        lead_id: UUID | None,
        wa_phone_number_id: str,
        wa_contact_phone: str,
        variant_id: str | None = None,
        profile_id: UUID | None = None,
    ) -> Conversation:
        # `profile_id` non passato → si adotta il profilo `is_default` del
        # merchant (ADR 0022). Se il merchant non ne ha definiti, resta None e la
        # conversazione gira esattamente come prima dei profili.
        if profile_id is None:
            profile_id = (
                await self._session.execute(
                    select(ConversationProfile.id).where(
                        ConversationProfile.merchant_id == merchant_id,
                        ConversationProfile.is_default.is_(True),
                        ConversationProfile.enabled.is_(True),
                    )
                )
            ).scalar_one_or_none()
        conv = Conversation(
            merchant_id=merchant_id,
            lead_id=lead_id,
            wa_phone_number_id=wa_phone_number_id,
            wa_contact_phone=wa_contact_phone,
            variant_id=variant_id,
            profile_id=profile_id,
            status="active",
        )
        self._session.add(conv)
        await self._session.flush()
        return conv

    async def touch_last_message(self, conversation_id: UUID) -> None:
        now = datetime.now(tz=UTC)
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(last_message_at=now, message_count=Conversation.message_count + 1)
        )

    async def touch_last_inbound(self, conversation_id: UUID) -> None:
        """Stamp the time of the last customer message — drives the 24h window.

        Called only when a genuinely new inbound message is persisted (not on
        outbound sends), so `last_inbound_at` reflects the start of the current
        customer-service window.
        """
        now = datetime.now(tz=UTC)
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(last_inbound_at=now)
        )

    async def list_reminder_candidates(self, *, min_idle_minutes: int) -> list[ReminderCandidate]:
        """Cross-tenant scan of conversations silent long enough to be a no-answer.

        ADR 0015: the scheduler is a pure edge-triggered emitter — it no longer
        tracks per-attempt cadence (that lives in the automation graph now), it
        just surfaces conversations idle past a floor, plus the
        `no_answer_fired_for` anchor so the caller emits `lead.no_answer` once per
        silence episode.

        **Il silenzio conta anche quando il lead non ha MAI risposto** (ADR 0025).
        Qui si richiedeva `last_inbound_at IS NOT NULL` — "solo una conversazione
        con un inbound può ammutolirsi" — che però descrive un caso solo: il lead
        che risponde e poi sparisce. Un primo contatto in uscita a cui non
        risponde nessuno è l'altro caso, ed è quello che i merchant chiamano
        davvero "nessuna risposta": su Recruiting DM erano 566 conversazioni
        attive su 567, tutte invisibili, e `lead.no_answer` non è mai stato
        emesso in produzione. Il filtro è caduto; l'ancora la sceglie il chiamante
        (`last_inbound_at` se c'è, altrimenti `started_at`).

        Resta invece `last_message_at IS NOT NULL`: senza un messaggio nostro non
        c'è nessun silenzio di cui il lead sia responsabile — sono conversazioni
        create dal CRM su cui non è mai partito niente, e vanno diagnosticate
        come tali, non sollecitate.

        `min_idle_minutes` non ha default di proposito: era 30, e una costante
        qui dentro sovrascriveva silenziosamente il `delay_minutes` che il
        merchant aveva impostato sul nodo trigger — qualunque valore sotto la
        mezz'ora non poteva funzionare. Ora il chiamante lo deriva dal minimo
        davvero configurato (`AutomationRepository.enabled_trigger_delays`), e
        questa query si limita a essere il filtro grezzo.

        Threads under human control are excluded: after a handoff the operator
        owns the conversation, and a "ci sei ancora?" follow-up landing on top of
        a human who is mid-call with the customer is exactly the silence the
        handoff was supposed to buy.
        """
        now = datetime.now(tz=UTC)
        idle_cutoff = now - timedelta(minutes=min_idle_minutes)

        # L'ultimo messaggio in uscita della conversazione, per la sola parte che
        # serve: il nome del template con cui è stato mandato. LATERAL e non una
        # sottoquery correlata nella SELECT perché ne serve più di una colonna, e
        # gira solo sulle righe che hanno superato i filtri.
        last_out = (
            select(Message.meta["template"]["name"].astext.label("template_name"))
            .where(Message.conversation_id == Conversation.id, Message.direction == "out")
            .order_by(Message.created_at.desc())
            .limit(1)
            .lateral("last_out")
        )

        stmt = (
            select(
                Conversation.id,
                Conversation.merchant_id,
                Merchant.tenant_id,
                Conversation.wa_phone_number_id,
                Conversation.wa_contact_phone,
                Conversation.last_message_at,
                Conversation.started_at,
                Conversation.last_inbound_at,
                Conversation.meta["no_answer_fired_for"].astext.label("no_answer_fired_for"),
                Lead.optimal_send_hour,
                Lead.name.label("lead_name"),
                last_out.c.template_name.label("last_outbound_template_name"),
                WhatsAppTemplate.id.label("last_outbound_template_id"),
            )
            .join(Merchant, Merchant.id == Conversation.merchant_id)
            .outerjoin(Lead, Lead.id == Conversation.lead_id)
            .outerjoin(last_out, true())
            # Il nome del template è quello di 360dialog; l'id è ciò che il nodo
            # trigger memorizza. `(merchant_id, name)` è unico, quindi il join
            # non può moltiplicare le righe.
            .outerjoin(
                WhatsAppTemplate,
                (WhatsAppTemplate.merchant_id == Conversation.merchant_id)
                & (WhatsAppTemplate.name == last_out.c.template_name),
            )
            .where(
                Conversation.status == "active",
                Conversation.last_message_at.is_not(None),
                Conversation.last_message_at < idle_cutoff,
                _bot_owns_thread(),
            )
            # Cap di sicurezza per tick. `order_by` esplicito: senza, una
            # piattaforma con più di 500 candidati restituirebbe un insieme
            # arbitrario a ogni giro e una conversazione poteva non uscire mai.
            # Le più vecchie prima = quelle che aspettano da più tempo.
            .order_by(Conversation.last_message_at)
            .limit(500)
        )
        rows = await self._session.execute(stmt)

        results: list[ReminderCandidate] = []
        for row in rows.mappings():
            results.append(
                ReminderCandidate(
                    conversation_id=row["id"],
                    merchant_id=row["merchant_id"],
                    tenant_id=row["tenant_id"],
                    wa_phone_number_id=row["wa_phone_number_id"] or "",
                    wa_contact_phone=row["wa_contact_phone"] or "",
                    last_message_at=row["last_message_at"],
                    started_at=row["started_at"],
                    last_inbound_at=row["last_inbound_at"],
                    optimal_send_hour=row["optimal_send_hour"],
                    lead_name=row["lead_name"],
                    no_answer_fired_for=_parse_anchor_map(row["no_answer_fired_for"]),
                    last_outbound_template_id=row["last_outbound_template_id"],
                    last_outbound_template_name=row["last_outbound_template_name"],
                )
            )
        return results

    async def close_idle_active(
        self,
        *,
        min_idle_minutes: int,
        followup_floor_by_merchant: dict[UUID, int] | None = None,
        limit: int = 500,
    ) -> list[UUID]:
        """Close active conversations with no activity for `min_idle_minutes`.

        There is no explicit 'conversation closed' event in the WhatsApp flow, so
        we approximate close = prolonged silence. Returns the ids that were
        closed so the caller can enqueue UC-13 objection extraction for each.

        **La chiusura deve stare dopo la finestra di follow-up (UC-03)**, perché
        `list_reminder_candidates` guarda solo le conversazioni `active`: chiudere
        per prime le silenziose significa rendere il trigger "nessuna risposta"
        irraggiungibile. Il docstring qui sopra affermava questa invariante ma
        nessuno la faceva rispettare, e con `conversation.idle_close_minutes` a
        120 e il default del nodo trigger anch'esso a 120 le due soglie
        coincidevano: qualunque automazione con ritardo ≥ 120 minuti non è mai
        partita. Ora l'invariante è esplicita — `followup_floor_by_merchant`
        porta, per merchant, il ritardo più lungo configurato sulla lavagnetta
        (più un margine), e una conversazione non si chiude prima di quello.

        Chiudere la conversazione è anche la **fine dell'episodio** nel senso di
        ADR 0022, quindi è qui che il profilo caricato da un'automazione decade e
        si torna al profilo `is_default` del merchant. Agganciarlo alla chiusura
        e non a uno stato di run è coerente col motore stateless (ADR 0015): non
        c'è nessun altro punto che sappia dire "l'episodio è finito".
        """
        floors = followup_floor_by_merchant or {}
        now = datetime.now(tz=UTC)
        cutoff = now - timedelta(minutes=min_idle_minutes)
        rows = (
            await self._session.execute(
                select(
                    Conversation.id,
                    Conversation.merchant_id,
                    Conversation.last_message_at,
                )
                .where(
                    Conversation.status == "active",
                    Conversation.last_message_at.is_not(None),
                    Conversation.last_message_at < cutoff,
                    # Una conversazione in attesa della riapertura non è
                    # silenziosa: è in coda per una risposta che abbiamo
                    # promesso al cliente. Con la soglia di default (120
                    # minuti) qualunque domanda arrivata la sera verrebbe
                    # chiusa nel cuore della notte, e lo sweep di ripresa —
                    # che guarda solo le `active` — non la troverebbe più.
                    # Stessa classe di errore dell'ordinamento chiusura /
                    # follow-up descritto qui sopra: due sweep che si
                    # calpestano perché nessuno dei due sa dell'altro.
                    Conversation.off_hours_pending_at.is_(None),
                )
                .order_by(Conversation.last_message_at)
                .limit(limit)
            )
        ).all()

        # Il filtro per-merchant sta in Python e non in SQL di proposito: i floor
        # sono pochi (uno per merchant con un'automazione no_answer attiva) e
        # tradurli in un CASE correlato renderebbe la query illeggibile senza
        # risparmiare nulla su un batch già limitato a `limit` righe.
        ids: list[UUID] = []
        for conv_id, merchant_id, last_message_at in rows:
            floor = floors.get(merchant_id)
            if floor is not None and now - last_message_at < timedelta(minutes=floor):
                continue
            ids.append(conv_id)

        if ids:
            await self._session.execute(
                update(Conversation).where(Conversation.id.in_(ids)).values(status="closed")
            )
            await self._reset_profiles_to_default(ids)
        return ids

    async def _reset_profiles_to_default(self, conversation_ids: list[UUID]) -> None:
        """Riporta le conversazioni chiuse al profilo di default del loro merchant.

        Una sola UPDATE con sottoquery correlata: il default varia per merchant e
        il batch ne contiene molti. Se un merchant non ha un profilo di default
        la sottoquery torna NULL, che è il valore giusto (nessun profilo).
        """
        default_for_merchant = (
            select(ConversationProfile.id)
            .where(
                ConversationProfile.merchant_id == Conversation.merchant_id,
                ConversationProfile.is_default.is_(True),
                ConversationProfile.enabled.is_(True),
            )
            .limit(1)
            .correlate(Conversation)
            .scalar_subquery()
        )
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id.in_(conversation_ids))
            .values(profile_id=default_for_merchant)
        )

    async def dropped_off_targets(
        self, conversation_ids: list[UUID]
    ) -> list[tuple[UUID, UUID, UUID]]:
        """UC-05 — leads to mark `dropped_off` among just-closed conversations.

        A conversation closed on prolonged silence (see `close_idle_active`) is
        treated as an abandonment *unless* it was handed off to a human (the lead
        didn't drop off, it escalated). Returns (lead_id, merchant_id, tenant_id)
        tuples so the caller can rescore each lead in its own tenant context.
        """
        if not conversation_ids:
            return []
        stmt = (
            select(Conversation.lead_id, Conversation.merchant_id, Merchant.tenant_id)
            .join(Merchant, Merchant.id == Conversation.merchant_id)
            .where(
                Conversation.id.in_(conversation_ids),
                Conversation.lead_id.is_not(None),
                Conversation.handoff_at.is_(None),
            )
        )
        rows = await self._session.execute(stmt)
        return [(row[0], row[1], row[2]) for row in rows.all()]

    async def merchants_with_conversations_before(
        self, cutoff: datetime
    ) -> list[tuple[UUID, UUID]]:
        """Distinct (merchant_id, tenant_id) that have at least one conversation
        started before `cutoff`. Used by the retention sweep to limit per-merchant
        config resolution to merchants that actually have purgeable data.
        """
        stmt = (
            select(Conversation.merchant_id, Merchant.tenant_id)
            .join(Merchant, Merchant.id == Conversation.merchant_id)
            .where(Conversation.started_at < cutoff)
            .distinct()
        )
        rows = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in rows.all()]

    async def delete_older_than(
        self, *, merchant_id: UUID, cutoff: datetime, limit: int = 2000
    ) -> int:
        """Hard-delete a merchant's conversations whose last activity predates
        `cutoff` (GDPR retention). Messages cascade via the FK ondelete. Capped at
        `limit` rows per call so a daily sweep stays bounded. Returns the count.
        """
        last_activity = func.coalesce(Conversation.last_message_at, Conversation.started_at)
        sub = (
            select(Conversation.id)
            .where(Conversation.merchant_id == merchant_id, last_activity < cutoff)
            .limit(limit)
        )
        ids = list((await self._session.execute(sub)).scalars().all())
        if not ids:
            return 0
        await self._session.execute(delete(Conversation).where(Conversation.id.in_(ids)))
        return len(ids)

    async def claim_handoff(
        self,
        conversation_id: UUID,
        *,
        reason: str | None = None,
        summary: str | None = None,
    ) -> bool:
        """Take the bot off the thread — but only if the bot still owns it
        (`auto_reply = true`). The one way a handoff episode is opened.

        A burst of inbounds (es. un album di 10 foto) fans out to concurrent
        turns that can all decide to escalate before the first flip commits; the
        row lock serializes them and only one UPDATE matches. Returns True when
        this caller won the claim — losers must NOT send another handoff message
        (the customer already got one) nor re-notify the operator.

        `handoff_summary` is overwritten, not coalesced: it is the AI's brief for
        *this* episode, and inheriting the previous one would put a stale summary
        in the operator's Slack alert.
        """
        result = await self._session.execute(
            text(
                """
                UPDATE conversations
                SET auto_reply = false,
                    handoff_at = now(),
                    handoff_resolved_at = NULL,
                    handoff_reason = :reason,
                    handoff_summary = :summary,
                    meta = coalesce(meta, '{}'::jsonb) || jsonb_build_object(
                        'escalated', true,
                        'escalated_at', now()::text,
                        'escalation_reason', CAST(:reason AS text),
                        -- Where the funnel was before the operator took over, so
                        -- resolving can put the bot back on the same step instead
                        -- of restarting it from qualification.
                        'state_before_handoff', coalesce(current_state, 'QUALIFYING')
                    )
                WHERE id = :conversation_id AND auto_reply = true
                RETURNING id
                """
            ),
            {"conversation_id": str(conversation_id), "reason": reason, "summary": summary},
        )
        return result.scalar_one_or_none() is not None

    async def claim_manual_handoff(self, conversation_id: UUID, *, reason: str) -> bool:
        """Handoff opened by a human, not by the AI (operator replies from the inbox).

        Same state as `claim_handoff` minus the AI brief, plus one difference that
        matters: the SLA anchor is pre-burned. The overdue sweep asks "has anyone
        picked this thread up?", and here the answer is yes by construction — the
        operator is the one who opened it. Without this, every manual reply would
        schedule a "handoff waiting" alert against the very person handling it.
        """
        result = await self._session.execute(
            text(
                """
                UPDATE conversations
                SET auto_reply = false,
                    handoff_at = now(),
                    handoff_resolved_at = NULL,
                    handoff_reason = :reason,
                    meta = coalesce(meta, '{}'::jsonb) || jsonb_build_object(
                        'escalated', true,
                        'escalated_at', now()::text,
                        'escalation_reason', CAST(:reason AS text),
                        'state_before_handoff', coalesce(current_state, 'QUALIFYING'),
                        'handoff_sla_fired_for', now()::text
                    )
                WHERE id = :conversation_id AND auto_reply = true
                RETURNING id
                """
            ),
            {"conversation_id": str(conversation_id), "reason": reason},
        )
        return result.scalar_one_or_none() is not None

    async def resolve_handoff(self, conversation_id: UUID) -> None:
        """Give the thread back to the bot — the exact inverse of a claim.

        Every field a claim touched has to be undone together. Flipping only
        `auto_reply` leaves the episode open (`handoff_resolved_at IS NULL`),
        which reads as "a human owns this" to the automation engine's `ai_paused`
        gate and to the overdue sweep: the thread answers inbound messages but
        stays permanently invisible to proactive automations, and keeps
        generating SLA alerts nobody can clear.

        `current_state` is restored too: ESCALATED is a sticky terminal FSM state
        whose prompt hint tells the model "the conversation is with a human
        operator, do not reply automatically". Left in place, the bot resumes
        with instructions not to. The claim stashed the pre-handoff step in
        `meta.state_before_handoff`, so the funnel picks up where it left off.
        """
        await self._session.execute(
            text(
                """
                UPDATE conversations
                SET auto_reply = true,
                    ai_disabled_until = NULL,
                    handoff_resolved_at = now(),
                    current_state = CASE
                        WHEN current_state = 'ESCALATED'
                        -- nullif: never restore ESCALATED onto itself, that would
                        -- re-arm the terminal state the reset exists to clear.
                        THEN coalesce(
                            nullif(meta ->> 'state_before_handoff', 'ESCALATED'),
                            'QUALIFYING'
                        )
                        ELSE current_state
                    END,
                    meta = coalesce(meta, '{}'::jsonb) || jsonb_build_object(
                        'escalated', false,
                        'handoff_resolved_at', now()::text
                    )
                WHERE id = :conversation_id
                """
            ),
            {"conversation_id": str(conversation_id)},
        )

    async def mark_no_answer_fired(
        self, conversation_id: UUID, automation_id: UUID, anchor: datetime
    ) -> None:
        """Timbra l'ancora per cui `lead.no_answer` è stato emesso, **per
        automazione** (ADR 0015/0029).

        Era un timestamp solo per conversazione. Non regge appena il merchant ha
        due automazioni "nessuna risposta" con ritardi diversi: la prima a
        maturare bruciava l'ancora e la seconda non partiva mai. Ora è una mappa
        `{automation_id: ancora}` e ogni automazione ha il suo episodio.

        Il `CASE` normalizza la forma vecchia: se il valore salvato non è un
        oggetto (era una stringa ISO), lo si sostituisce con una mappa vuota
        prima di scriverci dentro — `jsonb_set` su un percorso dentro una stringa
        non farebbe nulla, e l'ancora andrebbe persa a ogni tick.
        """
        await self._session.execute(
            text(
                """
                UPDATE conversations
                SET meta = jsonb_set(
                    CASE
                      WHEN jsonb_typeof(
                             coalesce(meta, '{}'::jsonb) -> 'no_answer_fired_for'
                           ) = 'object'
                      THEN coalesce(meta, '{}'::jsonb)
                      ELSE jsonb_set(
                             coalesce(meta, '{}'::jsonb),
                             '{no_answer_fired_for}',
                             '{}'::jsonb,
                             true
                           )
                    END,
                    ARRAY['no_answer_fired_for', :automation_id],
                    to_jsonb(CAST(:anchor AS text)),
                    true
                )
                WHERE id = :conversation_id
                """
            ),
            {
                "conversation_id": str(conversation_id),
                "automation_id": str(automation_id),
                "anchor": anchor.isoformat(),
            },
        )

    async def list_overdue_handoffs(
        self, *, min_overdue_minutes: int, limit: int = 500, max_age_hours: int = 24
    ) -> list[HandoffOverdueCandidate]:
        """Cross-tenant scan of open handoffs older than `min_overdue_minutes`.

        An open handoff is `handoff_at IS NOT NULL AND handoff_resolved_at IS NULL`
        (the same predicate the automation engine uses for `ai_paused`). Returns
        the `handoff_sla_fired_for` anchor so the caller emits the overdue trigger
        once per handoff episode (edge-triggered, mirroring no-answer).

        Two bounds keep the pool finite. Nothing ever resolves a handoff on the
        merchant's behalf, so without them every escalation the platform has ever
        made stays a candidate forever:

        - `status = 'active'`: the idle-close sweep closes silent threads without
          touching the handoff columns, and an SLA alert on a conversation that
          has been closed for weeks helps nobody.
        - `max_age_hours`: a handoff older than a day is a triage problem, not an
          SLA breach. The bound also stops the retroactive burst — a merchant who
          enables an overdue automation today would otherwise get one alert for
          every unresolved handoff in their history, because the emitter
          deliberately leaves the anchor unburned while nobody is listening.
        """
        now = datetime.now(tz=UTC)
        cutoff = now - timedelta(minutes=min_overdue_minutes)
        floor = now - timedelta(hours=max_age_hours)
        sla_fired = Conversation.meta["handoff_sla_fired_for"].astext
        stmt = (
            select(
                Conversation.id,
                Conversation.merchant_id,
                Merchant.tenant_id,
                Conversation.lead_id,
                Conversation.handoff_at,
                sla_fired.label("sla_fired_for"),
            )
            .join(Merchant, Merchant.id == Conversation.merchant_id)
            .where(
                Conversation.handoff_at.is_not(None),
                Conversation.handoff_resolved_at.is_(None),
                Conversation.handoff_at < cutoff,
                Conversation.handoff_at >= floor,
                Conversation.status == "active",
                # Exclude already-alerted handoffs in SQL (not just the Python edge
                # gate) so the pool self-drains: otherwise unresolved-but-alerted
                # rows accumulate forever and, past the 500 cap, could starve fresh
                # overdue handoffs out of the result set. `handoff_sla_fired_for` is
                # a new key we only ever write as an ISO timestamp, so the cast is
                # safe. Oldest-first so the most urgent are handled within the cap.
                or_(
                    sla_fired.is_(None),
                    cast(sla_fired, DateTime(timezone=True)) < Conversation.handoff_at,
                ),
            )
            .order_by(Conversation.handoff_at)
            .limit(limit)
        )
        rows = await self._session.execute(stmt)
        results: list[HandoffOverdueCandidate] = []
        for row in rows.mappings():
            results.append(
                HandoffOverdueCandidate(
                    conversation_id=row["id"],
                    merchant_id=row["merchant_id"],
                    tenant_id=row["tenant_id"],
                    lead_id=row["lead_id"],
                    handoff_at=row["handoff_at"],
                    sla_fired_for=_parse_iso(row["sla_fired_for"]),
                )
            )
        return results

    async def mark_handoff_sla_fired(self, conversation_id: UUID, anchor: datetime) -> None:
        """Stamp the `handoff_at` anchor a `conversation.handoff_overdue` trigger was
        emitted for. Suppresses re-emission until a new handoff (advancing
        `handoff_at`) re-arms it."""
        await self._session.execute(
            text(
                """
                UPDATE conversations
                SET meta = jsonb_set(
                    coalesce(meta, '{}'::jsonb),
                    '{handoff_sla_fired_for}',
                    to_jsonb(CAST(:anchor AS text))
                )
                WHERE id = :conversation_id
                """
            ),
            {"conversation_id": str(conversation_id), "anchor": anchor.isoformat()},
        )

    async def mark_off_hours_pending(self, conversation_id: UUID) -> bool:
        """Segna che una risposta è stata rimandata alla riapertura.

        `WHERE off_hours_pending_at IS NULL` rende la scrittura un *claim*: se
        il cliente manda cinque messaggi di notte, il marcatore resta quello
        del primo. Serve a due cose insieme — il messaggio di cortesia parte
        una volta sola (chi non vince il claim tace), e alla riapertura lo
        sweep recupera l'intera raffica invece dell'ultimo messaggio soltanto.

        Il timestamp viene dall'orologio del **database**, non da quello del
        processo, e va scritto nella stessa transazione che salva il messaggio
        in arrivo. Dentro una transazione Postgres `now()` è costante: marcatore
        e `created_at` del messaggio risultano identici, e il confronto
        `created_at >= off_hours_pending_at` dello sweep include il messaggio
        che ha aperto l'attesa. Con l'orologio del processo bastava un
        millisecondo di scarto in avanti perché la domanda da cui è nata tutta
        l'attesa restasse fuori dalla ripresa.

        Restituisce True solo a chi ha piazzato il marcatore.
        """
        result = await self._session.execute(
            text(
                """
                UPDATE conversations
                SET off_hours_pending_at = now()
                WHERE id = :conversation_id AND off_hours_pending_at IS NULL
                RETURNING id
                """
            ),
            {"conversation_id": str(conversation_id)},
        )
        return result.scalar_one_or_none() is not None

    async def claim_off_hours_resume(
        self, conversation_id: UUID, pending_since: datetime, *, stale_after_minutes: int = 15
    ) -> bool:
        """Prende in carico la ripresa di questa conversazione. Uno solo vince.

        Serve perché due passate dello sweep possono **sovrapporsi**: il giro
        processa fino a 500 conversazioni in sequenza, ognuna con una chiamata
        al modello, e il lunedì dopo un fine settimana quel giro dura più dei
        cinque minuti che separano un tick dal successivo. Senza questo claim
        la seconda passata ritroverebbe le stesse righe ancora marcate e il
        cliente riceverebbe **due** risposte alla riapertura — esattamente il
        contrario di «una risposta sola».

        Il claim scade da solo dopo `stale_after_minutes`: se il worker muore a
        metà, la ripresa non resta bloccata per sempre e il tick successivo la
        riprova. È il compromesso giusto nella direzione giusta — nel peggiore
        dei casi una risposta arriva in ritardo, mai una promessa che evapora.

        `off_hours_pending_at = :pending_since` è un compare-and-swap: se nel
        frattempo il marcatore è cambiato (episodio chiuso e riaperto), questo
        candidato è stantio e non deve partire.
        """
        result = await self._session.execute(
            text(
                """
                UPDATE conversations
                SET meta = jsonb_set(
                    coalesce(meta, '{}'::jsonb),
                    '{off_hours_resume_claimed_at}',
                    to_jsonb(now()::text)
                )
                WHERE id = :conversation_id
                  AND off_hours_pending_at = :pending_since
                  AND (
                        meta->>'off_hours_resume_claimed_at' IS NULL
                     OR (meta->>'off_hours_resume_claimed_at')::timestamptz
                        < now() - make_interval(mins => :stale_after)
                  )
                RETURNING id
                """
            ),
            {
                "conversation_id": str(conversation_id),
                "pending_since": pending_since,
                "stale_after": stale_after_minutes,
            },
        )
        return result.scalar_one_or_none() is not None

    async def release_off_hours_resume(self, conversation_id: UUID) -> None:
        """Molla il claim senza toccare il marcatore: la ripresa va riprovata."""
        await self._session.execute(
            text(
                """
                UPDATE conversations
                SET meta = coalesce(meta, '{}'::jsonb) - 'off_hours_resume_claimed_at'
                WHERE id = :conversation_id
                """
            ),
            {"conversation_id": str(conversation_id)},
        )

    async def human_replied_since(self, conversation_id: UUID, since: datetime) -> bool:
        """Un operatore in carne e ossa ha scritto su questo thread dopo `since`?

        È la condizione che impedisce al bot di rispondere alla riapertura sopra
        chi ha già risolto la questione durante la chiusura.

        Non basta guardare `sender_type='human'`, per due ragioni indipendenti:

        * **Lo storico è sbagliato.** Fino a poco fa l'endpoint del composer
          costruiva il messaggio senza passare `sender_type`, quindi prendeva il
          default della colonna (`'ai'`) e scriveva `'human'` solo dentro `meta`.
          Tutti i messaggi scritti a mano prima del fix sono indistinguibili dal
          bot se si guarda la sola colonna. `role='agent'` invece c'è sempre
          stato — il router lo passa esplicitamente — ed è il discriminante che
          copre anche il passato.
        * **`'phone'` conta come umano.** È il merchant che risponde dall'app
          WhatsApp del telefono (mirroring 360dialog), il modo più comune di
          rispondere a mano. Non apre un handoff: mette solo una pausa di due
          ore su `ai_disabled_until`, che dopo una notte di attesa è scaduta da
          un pezzo. Senza questo ramo il bot riprenderebbe la parola la mattina
          dopo su una conversazione che un umano aveva già chiuso all'una di
          notte — esattamente il caso che il requisito vieta.

        Gli altri modi in cui un operatore prende il thread (risposta dal
        composer, takeover manuale) spengono già `auto_reply` e aprono un
        handoff, quindi sono esclusi a monte da `list_off_hours_pending`. Questo
        controllo esiste per il buco che quelli lasciano aperto.

        Servito da `ix_messages_conv_created`.
        """
        stmt = select(
            exists().where(
                Message.conversation_id == conversation_id,
                Message.direction == "out",
                Message.created_at > since,
                or_(
                    Message.role == "agent",
                    Message.sender_type.in_(("human", "phone")),
                    Message.meta["sender_type"].astext.in_(("human", "phone")),
                ),
            )
        )
        return bool((await self._session.execute(stmt)).scalar())

    async def clear_off_hours_pending(self, conversation_id: UUID) -> None:
        """Chiude l'attesa: via il marcatore e via il claim che lo accompagnava.

        I due vanno insieme. Un claim lasciato indietro sopravviverebbe alla
        prossima chiusura e, finché non scade, bloccherebbe la ripresa di un
        episodio che non c'entra nulla con quello appena concluso.
        """
        await self._session.execute(
            text(
                """
                UPDATE conversations
                SET off_hours_pending_at = NULL,
                    meta = coalesce(meta, '{}'::jsonb) - 'off_hours_resume_claimed_at'
                WHERE id = :conversation_id
                """
            ),
            {"conversation_id": str(conversation_id)},
        )

    async def list_off_hours_pending(self, *, limit: int = 500) -> list[OffHoursPendingCandidate]:
        """Scansione cross-tenant delle conversazioni in attesa di riapertura.

        Volutamente *non* filtra per orario: quale merchant sia aperto adesso
        dipende dalla sua cascata di configurazione e dalla sua tabella orari,
        cioè da dati che questa query non può leggere senza una join per
        tenant. Il filtro orario lo applica lo sweep, merchant per merchant,
        su un insieme già ridotto dall'indice parziale.

        Esclude qui, in SQL, i casi in cui il bot non deve comunque parlare —
        handoff aperto, thread in takeover, conversazione chiusa — così il
        pool si drena da solo invece di riproporre ogni volta le stesse righe
        che poi verrebbero scartate in Python. Le più vecchie per prime: se il
        cap tronca, a restare indietro è chi ha aspettato meno.
        """
        stmt = (
            select(
                Conversation.id,
                Conversation.merchant_id,
                Merchant.tenant_id,
                Conversation.wa_phone_number_id,
                Conversation.wa_contact_phone,
                Conversation.off_hours_pending_at,
            )
            .join(Merchant, Merchant.id == Conversation.merchant_id)
            .where(
                Conversation.off_hours_pending_at.is_not(None),
                Conversation.status == "active",
                Conversation.auto_reply.is_(True),
                Conversation.wa_phone_number_id.is_not(None),
                Conversation.wa_contact_phone.is_not(None),
                _bot_owns_thread(),
            )
            .order_by(Conversation.off_hours_pending_at)
            .limit(limit)
        )
        rows = await self._session.execute(stmt)
        return [
            OffHoursPendingCandidate(
                conversation_id=row["id"],
                merchant_id=row["merchant_id"],
                tenant_id=row["tenant_id"],
                wa_phone_number_id=row["wa_phone_number_id"],
                wa_contact_phone=row["wa_contact_phone"],
                pending_since=row["off_hours_pending_at"],
            )
            for row in rows.mappings()
        ]

    async def update_state(self, conversation_id: UUID, state: str) -> None:
        """Persist the FSM current_state for the conversation."""
        await self._session.execute(
            text("UPDATE conversations SET current_state = :state WHERE id = :cid"),
            {"state": state, "cid": str(conversation_id)},
        )

    async def save_context_summary(self, conversation_id: UUID, summary: dict) -> None:
        """Persist the context compressor memory block."""
        import json

        await self._session.execute(
            text("UPDATE conversations SET context_summary = CAST(:s AS jsonb) WHERE id = :cid"),
            {"s": json.dumps(summary), "cid": str(conversation_id)},
        )
