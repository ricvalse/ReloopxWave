from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, Integer, String, cast, func, select, text, update
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
    name: str | None = None
    score: int = 0


class LeadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_phone(self, *, merchant_id: UUID, phone: str) -> Lead | None:
        stmt = select(Lead).where(Lead.merchant_id == merchant_id, Lead.phone == phone)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get(self, lead_id: UUID) -> Lead | None:
        return await self._session.get(Lead, lead_id)

    async def upsert_by_phone(
        self, *, merchant_id: UUID, phone: str, campaign: str | None = None
    ) -> Lead:
        """Get-or-create lead keyed on (merchant_id, phone). Returns the persisted row.

        `campaign` is recorded only when the row is first created (it's in the
        INSERT values, untouched by `on_conflict_do_nothing`), so a lead's
        original attribution is never overwritten by a later organic message.
        """
        stmt = (
            pg_insert(Lead)
            .values(merchant_id=merchant_id, phone=phone, campaign=campaign)
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

    async def update_sentiment(self, lead_id: UUID, *, sentiment: str) -> None:
        """Persist the latest turn-level sentiment label (UC-04 input)."""
        lead = await self._session.get(Lead, lead_id)
        if lead is None:
            return
        lead.sentiment = sentiment

    async def update_contact_fields(
        self, lead_id: UUID, *, name: str | None = None, email: str | None = None
    ) -> None:
        """Fill-only persistence of contact identity captured during a turn.

        Feeds UC-05 scoring (`has_name`/`has_email`) and the UC-04 contact note,
        and is the fallback source of `contact_fields` for booking/pipeline. Only
        writes a field that is currently empty, so a value the lead already
        confirmed is never clobbered by a later partial or re-asked answer.
        """
        if not name and not email:
            return
        lead = await self._session.get(Lead, lead_id)
        if lead is None:
            return
        if name and not lead.name:
            lead.name = name[:200]
        if email and not lead.email:
            lead.email = email[:320]

    async def merge_content_signals(
        self, lead_id: UUID, new_signals: dict[str, bool]
    ) -> dict[str, bool]:
        """Accumulate LLM-derived *content* signals on the lead (UC-05).

        OR-merges the truthy entries of `new_signals` into
        `lead.meta['content_signals']` and returns the full accumulated set, so a
        fact confirmed on an earlier turn (budget, timeline, an objection) is not
        lost when a later turn doesn't repeat it — keeping the score cumulative.
        """
        incoming = {k: True for k, v in new_signals.items() if v}
        lead = await self._session.get(Lead, lead_id)
        if lead is None:
            return incoming
        existing: dict[str, bool] = dict((lead.meta or {}).get("content_signals", {}))
        merged = {**existing, **incoming}
        if merged != existing:
            lead.meta = {**(lead.meta or {}), "content_signals": merged}
        return merged

    async def set_pipeline_stage(self, lead_id: UUID, *, stage_id: str) -> None:
        """Mirror a CRM-side pipeline-stage change back onto the lead (UC-04).

        Used by the GHL event router so the dashboard and scoring reflect moves
        made directly in GoHighLevel, not only those the bot initiates.
        """
        lead = await self._session.get(Lead, lead_id)
        if lead is None:
            return
        lead.pipeline_stage_id = stage_id

    async def get_by_ghl_contact_id(self, *, merchant_id: UUID, ghl_contact_id: str) -> Lead | None:
        """Resolve a lead by its GHL contact id (GHL webhooks key on it, not phone)."""
        stmt = select(Lead).where(
            Lead.merchant_id == merchant_id, Lead.ghl_contact_id == ghl_contact_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def anonymize_stale(
        self, *, merchant_id: UUID, cutoff: datetime, limit: int = 2000
    ) -> int:
        """GDPR retention at the lead level: strip PII from leads past the
        retention cutoff that have NO remaining conversations (their conversation
        content was already purged by the retention sweep, or they never
        engaged). Leads with a surviving — i.e. recent, kept — conversation are
        left untouched. Already-erased leads are skipped (idempotent).

        `phone` is NOT NULL + uniquely constrained per merchant, so it's set to a
        per-id tombstone rather than nulled; name/email/CRM id/sentiment are
        cleared and status flips to `erased`. Returns the number anonymized.
        """
        has_conversation = select(Conversation.id).where(Conversation.lead_id == Lead.id).exists()
        sub = (
            select(Lead.id)
            .where(
                Lead.merchant_id == merchant_id,
                Lead.created_at < cutoff,
                Lead.status != "erased",
                ~has_conversation,
            )
            .limit(limit)
        )
        ids = list((await self._session.execute(sub)).scalars().all())
        if not ids:
            return 0
        await self._session.execute(
            update(Lead)
            .where(Lead.id.in_(ids))
            .values(
                name=None,
                email=None,
                ghl_contact_id=None,
                sentiment=None,
                status="erased",
                phone=func.concat("erased:", cast(Lead.id, String)),
            )
        )
        return len(ids)

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
            select(
                Conversation.lead_id,
                Conversation.wa_phone_number_id,
                Conversation.auto_reply,
            )
            .distinct(Conversation.lead_id)
            .order_by(Conversation.lead_id, Conversation.last_message_at.desc())
            .subquery()
        )

        attempts_expr = cast(func.coalesce(Lead.meta["reactivation_attempts"].astext, "0"), Integer)
        last_attempt_raw = Lead.meta["last_reactivation_at"].astext

        last_attempt_ts = cast(last_attempt_raw, DateTime(timezone=True)).label(
            "last_reactivation_at"
        )

        stmt = (
            select(
                Lead.id,
                Lead.merchant_id,
                Merchant.tenant_id,
                Lead.phone,
                Lead.name,
                Lead.score,
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
                # UC-06: never reactivate opted-out, erased, or human-takeover leads.
                Lead.opted_out_at.is_(None),
                Lead.status != "erased",
                last_conv.c.auto_reply.is_(True),
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
                    name=row["name"],
                    score=int(row["score"] or 0),
                )
            )
        return results

    async def mark_opted_out(self, lead_id: UUID) -> bool:
        """Mark a lead opted-out (replied STOP/CANCELLA). Idempotent; returns True
        only on the transition so the caller can emit the event once."""
        lead = await self._session.get(Lead, lead_id)
        if lead is None or lead.opted_out_at is not None:
            return False
        lead.opted_out_at = datetime.now(tz=UTC)
        return True

    async def is_opted_out(self, *, merchant_id: UUID, phone: str) -> bool:
        stmt = select(Lead.opted_out_at).where(Lead.merchant_id == merchant_id, Lead.phone == phone)
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

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
