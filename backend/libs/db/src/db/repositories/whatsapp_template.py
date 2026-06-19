"""WhatsApp template persistence — CRUD + approval-status sync."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Merchant, WhatsAppTemplate


class WhatsAppTemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_merchant(
        self,
        merchant_id: UUID,
        *,
        purpose: str | None = None,
        status: str | None = None,
    ) -> list[WhatsAppTemplate]:
        stmt = select(WhatsAppTemplate).where(WhatsAppTemplate.merchant_id == merchant_id)
        if purpose:
            stmt = stmt.where(WhatsAppTemplate.purpose == purpose)
        if status:
            stmt = stmt.where(WhatsAppTemplate.status == status)
        stmt = stmt.order_by(WhatsAppTemplate.created_at.desc())
        return list((await self._session.execute(stmt)).scalars())

    async def get(self, template_id: UUID) -> WhatsAppTemplate | None:
        return await self._session.get(WhatsAppTemplate, template_id)

    async def get_by_name(self, merchant_id: UUID, name: str) -> WhatsAppTemplate | None:
        stmt = select(WhatsAppTemplate).where(
            WhatsAppTemplate.merchant_id == merchant_id,
            WhatsAppTemplate.name == name,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_approved_by_purpose(
        self, merchant_id: UUID, purpose: str
    ) -> WhatsAppTemplate | None:
        """Most recently created APPROVED template for a lifecycle purpose."""
        stmt = (
            select(WhatsAppTemplate)
            .where(
                WhatsAppTemplate.merchant_id == merchant_id,
                WhatsAppTemplate.purpose == purpose,
                WhatsAppTemplate.status == "approved",
            )
            .order_by(WhatsAppTemplate.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        merchant_id: UUID,
        name: str,
        category: str,
        language: str,
        purpose: str,
        body: str,
        variables: list[str],
        variable_sources: dict[str, str] | None = None,
        body_examples: list[str] | None = None,
        header_type: str = "NONE",
        header_text: str | None = None,
        header_image_url: str | None = None,
        footer: str | None = None,
        buttons: list[dict[str, Any]] | None = None,
        status: str = "pending_approval",
        whatsapp_template_id: str | None = None,
    ) -> WhatsAppTemplate:
        tpl = WhatsAppTemplate(
            merchant_id=merchant_id,
            name=name,
            category=category,
            language=language,
            purpose=purpose,
            body=body,
            variables=variables,
            variable_sources=variable_sources or {},
            body_examples=body_examples or [],
            header_type=header_type,
            header_text=header_text,
            header_image_url=header_image_url,
            footer=footer,
            buttons=buttons,
            status=status,
            whatsapp_template_id=whatsapp_template_id,
            submitted_at=datetime.now(tz=UTC) if status == "pending_approval" else None,
        )
        self._session.add(tpl)
        await self._session.flush()
        return tpl

    async def update(
        self,
        template: WhatsAppTemplate,
        *,
        category: str,
        language: str,
        purpose: str,
        body: str,
        variables: list[str],
        variable_sources: dict[str, str] | None = None,
        body_examples: list[str] | None = None,
        header_type: str = "NONE",
        header_text: str | None = None,
        header_image_url: str | None = None,
        footer: str | None = None,
        buttons: list[dict[str, Any]] | None = None,
    ) -> WhatsAppTemplate:
        """Edit a draft/rejected template's content and reset it to a clean draft.

        Meta can't edit a live template, so editing always lands the row back in
        `draft` and clears any stale Meta sync state — it must be re-submitted.
        """
        template.category = category
        template.language = language
        template.purpose = purpose
        template.body = body
        template.variables = variables
        template.variable_sources = variable_sources or {}
        template.body_examples = body_examples or []
        template.header_type = header_type
        template.header_text = header_text
        template.header_image_url = header_image_url
        template.footer = footer
        template.buttons = buttons
        template.status = "draft"
        template.meta_status = None
        template.meta_quality = None
        template.rejection_reason = None
        template.submitted_at = None
        template.approved_at = None
        template.rejected_at = None
        await self._session.flush()
        return template

    async def mark_submitted(
        self, template: WhatsAppTemplate, *, name: str, whatsapp_template_id: str | None = None
    ) -> WhatsAppTemplate:
        """Move a draft/rejected template into `pending_approval` after a submit."""
        template.name = name
        template.status = "pending_approval"
        template.meta_status = None
        template.rejection_reason = None
        template.rejected_at = None
        template.submitted_at = datetime.now(tz=UTC)
        if whatsapp_template_id:
            template.whatsapp_template_id = whatsapp_template_id
        await self._session.flush()
        return template

    async def list_pending(self, *, limit: int = 500) -> list[tuple[WhatsAppTemplate, UUID]]:
        """Cross-merchant scan of templates awaiting approval, for the sync cron.

        Returns `(template, tenant_id)` so the caller can resolve the per-merchant
        channel key. Runs under an admin (unscoped) session.
        """
        stmt = (
            select(WhatsAppTemplate, Merchant.tenant_id)
            .join(Merchant, Merchant.id == WhatsAppTemplate.merchant_id)
            .where(WhatsAppTemplate.status == "pending_approval")
            .limit(limit)
        )
        rows = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in rows.all()]

    async def apply_status(
        self,
        template: WhatsAppTemplate,
        *,
        local_status: str,
        meta_status: str | None = None,
        quality: str | None = None,
        rejection_reason: str | None = None,
        whatsapp_template_id: str | None = None,
    ) -> None:
        """Apply a synced approval status to a template row."""
        now = datetime.now(tz=UTC)
        template.status = local_status
        template.meta_status = meta_status
        template.meta_last_synced_at = now
        if quality is not None:
            template.meta_quality = quality
        if local_status == "rejected":
            # Only rejection carries a reason; record it when present.
            if rejection_reason is not None:
                template.rejection_reason = rejection_reason
        else:
            # Approval (or any non-rejected transition) clears a stale reason so
            # a re-approved template doesn't keep showing its old rejection text.
            template.rejection_reason = None
        if whatsapp_template_id and not template.whatsapp_template_id:
            template.whatsapp_template_id = whatsapp_template_id
        if local_status == "approved" and template.approved_at is None:
            template.approved_at = now
        if local_status == "rejected" and template.rejected_at is None:
            template.rejected_at = now
        await self._session.flush()

    async def apply_status_by_name(
        self,
        *,
        merchant_id: UUID | None,
        name: str,
        local_status: str,
        meta_status: str | None = None,
        rejection_reason: str | None = None,
        whatsapp_template_id: str | None = None,
    ) -> bool:
        """Apply a status update keyed by Meta template id (preferred) or name.

        Webhook payloads don't carry our merchant_id, so `merchant_id` is None on
        that path. Meta's `message_template_id` is globally unique, so when it is
        present we match on it first — this is unambiguous even if two merchants
        ever shared a template name. We fall back to name (our generated names
        embed a per-merchant prefix, so they're unique in practice too).
        """
        template: WhatsAppTemplate | None = None
        if whatsapp_template_id:
            id_stmt = select(WhatsAppTemplate).where(
                WhatsAppTemplate.whatsapp_template_id == whatsapp_template_id
            )
            if merchant_id is not None:
                id_stmt = id_stmt.where(WhatsAppTemplate.merchant_id == merchant_id)
            template = (await self._session.execute(id_stmt)).scalars().first()
        if template is None:
            stmt = select(WhatsAppTemplate).where(WhatsAppTemplate.name == name)
            if merchant_id is not None:
                stmt = stmt.where(WhatsAppTemplate.merchant_id == merchant_id)
            template = (await self._session.execute(stmt)).scalars().first()
        if template is None:
            return False
        await self.apply_status(
            template,
            local_status=local_status,
            meta_status=meta_status,
            rejection_reason=rejection_reason,
            whatsapp_template_id=whatsapp_template_id,
        )
        return True

    async def delete(self, template: WhatsAppTemplate) -> None:
        await self._session.delete(template)
        await self._session.flush()
