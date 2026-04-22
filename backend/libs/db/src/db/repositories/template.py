from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import BotTemplate


class BotTemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_tenant(self, tenant_id: UUID) -> list[BotTemplate]:
        stmt = select(BotTemplate).where(BotTemplate.tenant_id == tenant_id)
        return list((await self._session.execute(stmt)).scalars())

    async def get(self, template_id: UUID) -> BotTemplate | None:
        return await self._session.get(BotTemplate, template_id)

    async def create(
        self,
        *,
        tenant_id: UUID,
        name: str,
        description: str | None,
        defaults: dict[str, Any],
        locked_keys: list[str],
        is_default: bool = False,
    ) -> BotTemplate:
        if is_default:
            await self._clear_default(tenant_id)

        tmpl = BotTemplate(
            tenant_id=tenant_id,
            name=name,
            description=description,
            defaults=defaults,
            locked_keys=locked_keys,
            is_default=is_default,
        )
        self._session.add(tmpl)
        await self._session.flush()
        return tmpl

    async def update(
        self,
        template_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        defaults: dict[str, Any] | None = None,
        locked_keys: list[str] | None = None,
        is_default: bool | None = None,
    ) -> BotTemplate | None:
        tmpl = await self._session.get(BotTemplate, template_id)
        if tmpl is None:
            return None
        if is_default:
            await self._clear_default(tmpl.tenant_id)
        if name is not None:
            tmpl.name = name
        if description is not None:
            tmpl.description = description
        if defaults is not None:
            tmpl.defaults = defaults
        if locked_keys is not None:
            tmpl.locked_keys = locked_keys
        if is_default is not None:
            tmpl.is_default = is_default
        await self._session.flush()
        return tmpl

    async def _clear_default(self, tenant_id: UUID) -> None:
        await self._session.execute(
            update(BotTemplate)
            .where(BotTemplate.tenant_id == tenant_id, BotTemplate.is_default.is_(True))
            .values(is_default=False)
        )
