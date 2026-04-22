from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config_resolver.schema import SYSTEM_DEFAULTS, ConfigKey
from db.models import BotConfig, BotTemplate, Merchant
from shared import get_logger

logger = get_logger(__name__)

CACHE_TTL_SECONDS = 60


class ConfigResolver:
    """Three-level cascade: merchant override → agency default → system default.

    Every lookup round-trips through Redis with a short TTL. Cache invalidation
    happens on write at the matching level. The TTL is a safety net, not the
    primary correctness mechanism.
    """

    def __init__(self, session: AsyncSession, redis: Redis | None = None) -> None:
        self._session = session
        self._redis = redis

    async def resolve(self, key: ConfigKey | str, *, merchant_id: UUID) -> Any:
        key_str = key.value if isinstance(key, ConfigKey) else key
        cache_key = f"cfg:{merchant_id}:{key_str}"

        if self._redis is not None:
            cached = await self._redis.get(cache_key)
            if cached is not None:
                return json.loads(cached)

        # 1. Merchant override
        cfg = await self._session.execute(
            select(BotConfig).where(BotConfig.merchant_id == merchant_id)
        )
        bot_cfg = cfg.scalar_one_or_none()
        if bot_cfg is not None:
            value = _lookup(bot_cfg.overrides, key_str)
            if value is not None:
                await self._cache(cache_key, value)
                return value

        # 2. Agency default (resolve the merchant's tenant first, then their default template)
        tenant_id_row = await self._session.execute(
            select(Merchant.tenant_id).where(Merchant.id == merchant_id)
        )
        tenant_id = tenant_id_row.scalar_one_or_none()
        if tenant_id is None:
            raise ValueError(f"Merchant {merchant_id} does not exist")

        tmpl = await self._session.execute(
            select(BotTemplate).where(
                BotTemplate.tenant_id == tenant_id,
                BotTemplate.is_default.is_(True),
            )
        )
        template = tmpl.scalar_one_or_none()
        if template is not None:
            value = _lookup(template.defaults, key_str)
            if value is not None:
                await self._cache(cache_key, value)
                return value

        # 3. System default
        value = SYSTEM_DEFAULTS.get(ConfigKey(key_str)) if key_str in {k.value for k in ConfigKey} else None
        await self._cache(cache_key, value)
        return value

    async def invalidate(self, merchant_id: UUID, *, keys: list[str] | None = None) -> None:
        if self._redis is None:
            return
        if keys is None:
            pattern = f"cfg:{merchant_id}:*"
            async for raw in self._redis.scan_iter(match=pattern):
                await self._redis.delete(raw)
        else:
            await self._redis.delete(*[f"cfg:{merchant_id}:{k}" for k in keys])

    async def _cache(self, key: str, value: Any) -> None:
        if self._redis is None:
            return
        await self._redis.set(key, json.dumps(value), ex=CACHE_TTL_SECONDS)


def _lookup(bag: dict, dotted_key: str) -> Any:
    """Walks a dotted key (`a.b.c`) through a nested JSON bag."""
    node: Any = bag
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


async def resolve(
    session: AsyncSession,
    redis: Redis | None,
    *,
    merchant_id: UUID,
    key: ConfigKey | str,
) -> Any:
    return await ConfigResolver(session, redis).resolve(key, merchant_id=merchant_id)
