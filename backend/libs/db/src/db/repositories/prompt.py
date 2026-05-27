from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.bot import PromptTemplate


class PromptRepository:
    """Reads versioned, variant-scoped prompt templates (spec 6.2 / UC-09)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_body(
        self,
        *,
        merchant_id: UUID,
        kind: str = "system",
        variant_id: str = "default",
    ) -> str | None:
        """Highest active version of a prompt for (merchant, kind, variant), or None.

        Returns the newest `version` so editing a prompt = inserting a new
        version row, keeping prior versions for audit/rollback.
        """
        stmt = (
            select(PromptTemplate.body)
            .where(
                PromptTemplate.merchant_id == merchant_id,
                PromptTemplate.kind == kind,
                PromptTemplate.variant_id == variant_id,
                PromptTemplate.is_active.is_(True),
            )
            .order_by(PromptTemplate.version.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert_system_prompt(
        self, *, merchant_id: UUID, variant_id: str, body: str, kind: str = "system"
    ) -> UUID:
        """Author/replace the prompt for (merchant, kind, variant).

        Modeled as append-a-version: deactivate prior versions, then insert a
        new active row at version = max(prior)+1. Prior versions stay for audit.
        Returns the new template id (callers may store it on the A/B variant).
        """
        await self._session.execute(
            update(PromptTemplate)
            .where(
                PromptTemplate.merchant_id == merchant_id,
                PromptTemplate.kind == kind,
                PromptTemplate.variant_id == variant_id,
                PromptTemplate.is_active.is_(True),
            )
            .values(is_active=False)
        )
        max_version = (
            await self._session.execute(
                select(func.coalesce(func.max(PromptTemplate.version), 0)).where(
                    PromptTemplate.merchant_id == merchant_id,
                    PromptTemplate.kind == kind,
                    PromptTemplate.variant_id == variant_id,
                )
            )
        ).scalar_one()
        row = PromptTemplate(
            merchant_id=merchant_id,
            kind=kind,
            variant_id=variant_id,
            version=int(max_version) + 1,
            body=body,
            is_active=True,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id
