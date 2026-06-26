from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Integer, cast, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Conversation, Lead, Merchant


@dataclass(slots=True, frozen=True)
class ReminderCandidate:
    conversation_id: UUID
    merchant_id: UUID
    tenant_id: UUID
    wa_phone_number_id: str
    wa_contact_phone: str
    last_message_at: datetime
    reminders_sent: int
    last_reminder_at: datetime | None
    # Last inbound (customer) message — drives the 24h window decision. May be
    # None on legacy rows created before migration 0014.
    last_inbound_at: datetime | None = None
    # S-05: preferred send hour (0-23) learned by the send-time optimizer.
    optimal_send_hour: int | None = None


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
    ) -> Conversation:
        conv = Conversation(
            merchant_id=merchant_id,
            lead_id=lead_id,
            wa_phone_number_id=wa_phone_number_id,
            wa_contact_phone=wa_contact_phone,
            variant_id=variant_id,
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

    async def list_reminder_candidates(
        self, *, max_followups: int, min_idle_minutes: int = 30
    ) -> list[ReminderCandidate]:
        """Cross-tenant scan of conversations that might need a follow-up.

        Applies a conservative floor on idle time so we don't pull every active
        conversation on every tick. The per-merchant reminder threshold is
        applied later, after the config cascade is resolved.
        """
        now = datetime.now(tz=UTC)
        idle_cutoff = now - timedelta(minutes=min_idle_minutes)

        reminders_sent_expr = cast(
            func.coalesce(Conversation.meta["reminders_sent"].astext, "0"),
            Integer,
        )
        stmt = (
            select(
                Conversation.id,
                Conversation.merchant_id,
                Merchant.tenant_id,
                Conversation.wa_phone_number_id,
                Conversation.wa_contact_phone,
                Conversation.last_message_at,
                Conversation.last_inbound_at,
                reminders_sent_expr.label("reminders_sent"),
                Conversation.meta["last_reminder_at"].astext.label("last_reminder_at"),
                Lead.optimal_send_hour,
            )
            .join(Merchant, Merchant.id == Conversation.merchant_id)
            .outerjoin(Lead, Lead.id == Conversation.lead_id)
            .where(
                Conversation.status == "active",
                Conversation.last_message_at.is_not(None),
                Conversation.last_message_at < idle_cutoff,
                reminders_sent_expr < max_followups,
            )
            .limit(500)  # safety cap per tick
        )
        rows = await self._session.execute(stmt)

        results: list[ReminderCandidate] = []
        for row in rows.mappings():
            last_rem = row["last_reminder_at"]
            if isinstance(last_rem, str):
                try:
                    last_reminder_at = datetime.fromisoformat(last_rem.replace("Z", "+00:00"))
                except ValueError:
                    last_reminder_at = None
            else:
                last_reminder_at = None
            results.append(
                ReminderCandidate(
                    conversation_id=row["id"],
                    merchant_id=row["merchant_id"],
                    tenant_id=row["tenant_id"],
                    wa_phone_number_id=row["wa_phone_number_id"] or "",
                    wa_contact_phone=row["wa_contact_phone"] or "",
                    last_message_at=row["last_message_at"],
                    reminders_sent=int(row["reminders_sent"]),
                    last_reminder_at=last_reminder_at,
                    last_inbound_at=row["last_inbound_at"],
                    optimal_send_hour=row["optimal_send_hour"],
                )
            )
        return results

    async def close_idle_active(self, *, min_idle_minutes: int, limit: int = 500) -> list[UUID]:
        """Close active conversations with no activity for `min_idle_minutes`.

        There is no explicit 'conversation closed' event in the WhatsApp flow, so
        we approximate close = prolonged silence. Returns the ids that were
        closed so the caller can enqueue UC-13 objection extraction for each.
        The threshold must sit *after* the follow-up window (UC-03) so we don't
        cut off pending reminders.
        """
        cutoff = datetime.now(tz=UTC) - timedelta(minutes=min_idle_minutes)
        ids = list(
            (
                await self._session.execute(
                    select(Conversation.id)
                    .where(
                        Conversation.status == "active",
                        Conversation.last_message_at.is_not(None),
                        Conversation.last_message_at < cutoff,
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        if ids:
            await self._session.execute(
                update(Conversation).where(Conversation.id.in_(ids)).values(status="closed")
            )
        return ids

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

    async def mark_escalated(
        self,
        conversation_id: UUID,
        *,
        reason: str | None = None,
        summary: str | None = None,
    ) -> None:
        """Human takeover (escalate_human action): silence the bot on this thread
        and stamp handoff state so the merchant inbox can triage it.

        Sets `auto_reply = false` (AND-ed with the merchant master switch in the
        worker, so the bot stays silent regardless) and writes the structured
        handoff columns (`handoff_at`, `handoff_reason`, `handoff_summary` — the
        AI's brief for the operator). The legacy `meta.escalated*` keys are kept
        for backward compatibility. The thread stays `active` — it still needs a
        human, it isn't closed.
        """
        await self._session.execute(
            text(
                """
                UPDATE conversations
                SET auto_reply = false,
                    handoff_at = now(),
                    handoff_resolved_at = NULL,
                    handoff_reason = :reason,
                    handoff_summary = coalesce(:summary, handoff_summary),
                    meta = jsonb_set(
                        jsonb_set(
                            jsonb_set(
                                coalesce(meta, '{}'::jsonb),
                                '{escalated}', 'true'::jsonb
                            ),
                            '{escalated_at}', to_jsonb(now()::text)
                        ),
                        '{escalation_reason}', to_jsonb(:reason::text)
                    )
                WHERE id = :conversation_id
                """
            ),
            {"conversation_id": str(conversation_id), "reason": reason, "summary": summary},
        )

    async def record_reminder_sent(self, conversation_id: UUID) -> None:
        """Atomically increment reminders_sent and stamp last_reminder_at in conversation.meta.

        Uses jsonb_set with the existing counter so concurrent workers don't clobber each other.
        """
        await self._session.execute(
            text(
                """
                UPDATE conversations
                SET meta = jsonb_set(
                    jsonb_set(
                        coalesce(meta, '{}'::jsonb),
                        '{reminders_sent}',
                        to_jsonb(
                            coalesce((meta ->> 'reminders_sent')::int, 0) + 1
                        )
                    ),
                    '{last_reminder_at}',
                    to_jsonb(now()::text)
                )
                WHERE id = :conversation_id
                """
            ),
            {"conversation_id": str(conversation_id)},
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
            text(
                "UPDATE conversations SET context_summary = :s::jsonb WHERE id = :cid"
            ),
            {"s": json.dumps(summary), "cid": str(conversation_id)},
        )
