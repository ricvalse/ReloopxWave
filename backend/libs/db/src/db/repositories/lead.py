from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Integer, cast, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Conversation, Lead, Merchant


@dataclass(slots=True, frozen=True)
class ReactivationCandidate:
    lead_id: UUID
    merchant_id: UUID
    tenant_id: UUID
    phone: str
    wa_phone_number_id: str
    last_interaction_at: datetime
    attempts_sent: int
    last_reactivation_at: datetime | None


class LeadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_phone(self, *, merchant_id: UUID, phone: str) -> Lead | None:
        stmt = select(Lead).where(Lead.merchant_id == merchant_id, Lead.phone == phone)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert_by_phone(self, *, merchant_id: UUID, phone: str) -> Lead:
        """Get-or-create lead keyed on (merchant_id, phone). Returns the persisted row."""
        stmt = (
            pg_insert(Lead)
            .values(merchant_id=merchant_id, phone=phone)
            .on_conflict_do_nothing(index_elements=["merchant_id", "phone"])
            .returning(Lead.id)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        if row is not None:
            lead = await self._session.get(Lead, row.id)
            assert lead is not None
            return lead

        existing = await self.get_by_phone(merchant_id=merchant_id, phone=phone)
        assert existing is not None, "upsert fell through both branches"
        return existing

    async def update_score(self, lead_id: UUID, *, score: int, reasons: list[str]) -> None:
        lead = await self._session.get(Lead, lead_id)
        if lead is None:
            return
        lead.score = score
        lead.score_reasons = reasons

    async def list_reactivation_candidates(
        self,
        *,
        dormant_cutoff: datetime,
        interval_cutoff: datetime,
        max_attempts: int,
    ) -> list[ReactivationCandidate]:
        """Cross-tenant scan of leads due for a reactivation attempt (UC-06)."""
        latest_conv_subq = (
            select(
                Conversation.lead_id,
                func.max(Conversation.last_message_at).label("last_interaction"),
            )
            .where(Conversation.last_message_at.is_not(None))
            .group_by(Conversation.lead_id)
            .subquery()
        )

        last_conv = (
            select(Conversation.lead_id, Conversation.wa_phone_number_id)
            .distinct(Conversation.lead_id)
            .order_by(Conversation.lead_id, Conversation.last_message_at.desc())
            .subquery()
        )

        attempts_expr = cast(
            func.coalesce(Lead.meta["reactivation_attempts"].astext, "0"), Integer
        )
        last_attempt_raw = Lead.meta["last_reactivation_at"].astext

        last_attempt_ts = cast(last_attempt_raw, DateTime(timezone=True)).label("last_reactivation_at")

        stmt = (
            select(
                Lead.id,
                Lead.merchant_id,
                Merchant.tenant_id,
                Lead.phone,
                last_conv.c.wa_phone_number_id,
                latest_conv_subq.c.last_interaction,
                attempts_expr.label("attempts_sent"),
                last_attempt_ts,
            )
            .join(Merchant, Merchant.id == Lead.merchant_id)
            .join(latest_conv_subq, latest_conv_subq.c.lead_id == Lead.id)
            .join(last_conv, last_conv.c.lead_id == Lead.id)
            .where(
                latest_conv_subq.c.last_interaction < dormant_cutoff,
                attempts_expr < max_attempts,
                (
                    last_attempt_raw.is_(None)
                    | (cast(last_attempt_raw, DateTime(timezone=True)) < interval_cutoff)
                ),
            )
            .limit(500)
        )
        rows = await self._session.execute(stmt)
        results: list[ReactivationCandidate] = []
        for row in rows.mappings():
            results.append(
                ReactivationCandidate(
                    lead_id=row["id"],
                    merchant_id=row["merchant_id"],
                    tenant_id=row["tenant_id"],
                    phone=row["phone"],
                    wa_phone_number_id=row["wa_phone_number_id"] or "",
                    last_interaction_at=row["last_interaction"],
                    attempts_sent=int(row["attempts_sent"]),
                    last_reactivation_at=row["last_reactivation_at"],
                )
            )
        return results

    async def record_reactivation_sent(self, lead_id: UUID) -> None:
        await self._session.execute(
            text(
                """
                UPDATE leads
                SET meta = jsonb_set(
                    jsonb_set(
                        coalesce(meta, '{}'::jsonb),
                        '{reactivation_attempts}',
                        to_jsonb(
                            coalesce((meta ->> 'reactivation_attempts')::int, 0) + 1
                        )
                    ),
                    '{last_reactivation_at}',
                    to_jsonb(now()::text)
                )
                WHERE id = :lead_id
                """
            ),
            {"lead_id": str(lead_id)},
        )
