from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Integer, cast, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Conversation, Merchant


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
                reminders_sent_expr.label("reminders_sent"),
                Conversation.meta["last_reminder_at"].astext.label("last_reminder_at"),
            )
            .join(Merchant, Merchant.id == Conversation.merchant_id)
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
                )
            )
        return results

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
