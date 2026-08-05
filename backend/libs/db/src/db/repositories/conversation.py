from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, DateTime, cast, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Conversation, ConversationProfile, Lead, Merchant


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
    # ADR 0015 edge-trigger anchor: the `last_inbound_at` we last emitted a
    # `lead.no_answer` trigger for. None = never fired for this conversation.
    no_answer_fired_for: datetime | None = None


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
            )
            .join(Merchant, Merchant.id == Conversation.merchant_id)
            .outerjoin(Lead, Lead.id == Conversation.lead_id)
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
                    no_answer_fired_for=_parse_iso(row["no_answer_fired_for"]),
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
                        'escalation_reason', :reason::text,
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
                        'escalation_reason', :reason::text,
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

    async def mark_no_answer_fired(self, conversation_id: UUID, anchor: datetime) -> None:
        """Stamp the `last_inbound_at` anchor a `lead.no_answer` trigger was emitted
        for (ADR 0015). The scheduler suppresses re-emission until the lead sends a
        new inbound (advancing `last_inbound_at` past this anchor)."""
        await self._session.execute(
            text(
                """
                UPDATE conversations
                SET meta = jsonb_set(
                    coalesce(meta, '{}'::jsonb),
                    '{no_answer_fired_for}',
                    to_jsonb(:anchor::text)
                )
                WHERE id = :conversation_id
                """
            ),
            {"conversation_id": str(conversation_id), "anchor": anchor.isoformat()},
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
                    to_jsonb(:anchor::text)
                )
                WHERE id = :conversation_id
                """
            ),
            {"conversation_id": str(conversation_id), "anchor": anchor.isoformat()},
        )

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
            text("UPDATE conversations SET context_summary = :s::jsonb WHERE id = :cid"),
            {"s": json.dumps(summary), "cid": str(conversation_id)},
        )
