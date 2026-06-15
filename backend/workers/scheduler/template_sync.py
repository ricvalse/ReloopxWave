"""WhatsApp template approval-status sync.

Two paths, mirroring the plan:
  * `apply_template_status_event` — webhook-driven, single template update keyed
    by name (the router forwards Meta's `message_template_status_update`).
  * `template_status_sync` — hourly cron fallback that polls 360dialog for every
    template still `pending_approval`, in case a webhook was missed.

Both run under an admin (unscoped) session — template rows are scanned across
merchants, and the webhook carries no JWT.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from db import (
    IntegrationRepository,
    WhatsAppTemplateRepository,
    session_scope,
)
from integrations.whatsapp.d360_templates import D360TemplateClient, map_meta_status_to_local
from shared import get_logger

logger = get_logger(__name__)


async def apply_template_status_event(
    ctx: dict[str, Any],
    name: str,
    event: str,
    reason: str | None = None,
    template_id: str | None = None,
) -> dict[str, Any]:
    """Apply a single webhook-delivered template status update (keyed by name)."""
    local = map_meta_status_to_local(event)
    async with session_scope() as session:
        updated = await WhatsAppTemplateRepository(session).apply_status_by_name(
            merchant_id=None,
            name=name,
            local_status=local,
            meta_status=event,
            rejection_reason=reason,
            whatsapp_template_id=template_id,
        )
    logger.info("template.status.webhook", name=name, meta_event=event, updated=updated)
    return {"updated": updated, "name": name, "status": local}


async def template_status_sync(ctx: dict[str, Any]) -> dict[str, Any]:
    """Poll 360dialog for every template still awaiting approval."""
    settings = ctx["settings"]
    kek = settings.integrations_kek_base64

    async with session_scope() as session:
        pending = await WhatsAppTemplateRepository(session).list_pending()
    # Carry primitives out of the session.
    items = [(tpl.id, tpl.merchant_id, tpl.name) for tpl, _ in pending]
    logger.info("template.sync.scan", count=len(items))

    # Resolve each merchant's channel once.
    channels: dict[UUID, tuple[str, str | None] | None] = {}
    async with session_scope() as session:
        integrations = IntegrationRepository(session, kek_base64=kek)
        for _, merchant_id, _ in items:
            if merchant_id in channels:
                continue
            wa = await integrations.resolve_whatsapp_by_merchant(merchant_id)
            channels[merchant_id] = (wa.api_key, wa.waba_base_url) if wa else None

    updated = 0
    for _tpl_id, merchant_id, name in items:
        chan = channels.get(merchant_id)
        if chan is None:
            continue
        api_key, base_url = chan
        client = D360TemplateClient(api_key=api_key, base_url=base_url)
        try:
            status = await client.fetch_template_status(name=name)
        except Exception as exc:  # network / provider error — try again next tick
            logger.warning("template.sync.fetch_failed", name=name, error=str(exc))
            continue
        finally:
            await client.close()

        if status is None:
            continue
        local = map_meta_status_to_local(status.status)
        async with session_scope() as session:
            ok = await WhatsAppTemplateRepository(session).apply_status_by_name(
                merchant_id=merchant_id,
                name=name,
                local_status=local,
                meta_status=status.status,
                rejection_reason=status.rejected_reason,
                whatsapp_template_id=status.whatsapp_template_id,
            )
        if ok:
            updated += 1

    return {"pending": len(items), "updated": updated}
