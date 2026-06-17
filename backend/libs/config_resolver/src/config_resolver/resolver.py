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

# Suffix for the whole-bag cache entry written by `resolve_all`. Namespaced
# per merchant exactly like the per-key entries (`cfg:{merchant_id}:{key}`),
# so the `cfg:{merchant_id}:*` invalidation scan covers it too.
RESOLVED_CACHE_KEY = "__resolved__"

# Process-wide Redis client, set once at startup (API lifespan / worker
# startup) via `set_shared_redis`. Any `ConfigResolver` built without an
# explicit `redis` picks this up, so caching + invalidation work uniformly
# across routers, the conversation pipeline, and action handlers without
# threading a client through every layer. Stays None in tests (no Redis) —
# the resolver then reads straight from the DB, which is always fresh.
_shared_redis: Redis | None = None


def set_shared_redis(redis: Redis | None) -> None:
    global _shared_redis
    _shared_redis = redis


def get_shared_redis() -> Redis | None:
    return _shared_redis


class ConfigResolver:
    """Three-level cascade: merchant override → agency default → system default.

    Every lookup round-trips through Redis with a short TTL. Cache invalidation
    happens on write at the matching level. The TTL is a safety net, not the
    primary correctness mechanism — every Redis op is best-effort and degrades
    to a direct DB read if Redis is unreachable.
    """

    def __init__(self, session: AsyncSession, redis: Redis | None = None) -> None:
        self._session = session
        self._redis = redis if redis is not None else _shared_redis

    async def resolve(self, key: ConfigKey | str, *, merchant_id: UUID) -> Any:
        key_str = key.value if isinstance(key, ConfigKey) else key
        cache_key = f"cfg:{merchant_id}:{key_str}"

        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
                if cached is not None:
                    return json.loads(cached)
            except Exception as e:  # Redis down / network blip → fall back to DB.
                logger.warning("config.cache.get_failed", key=cache_key, error=str(e))

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
        value = (
            SYSTEM_DEFAULTS.get(ConfigKey(key_str))
            if key_str in {k.value for k in ConfigKey}
            else None
        )
        await self._cache(cache_key, value)
        return value

    async def resolve_all(self, *, merchant_id: UUID) -> dict[str, Any]:
        """Resolve every ``ConfigKey`` for a merchant in a single pass.

        One Redis read of the whole bag (``cfg:{merchant_id}:__resolved__``) on
        a hit, or ≤3 DB queries on a miss — versus one ``resolve()`` round-trip
        per key (58 keys → up to ~174 queries cold). Returns a flat dict keyed
        by ``ConfigKey.value`` (dotted), ready to feed ``_dotted_set``. Cascade
        and None-skip semantics match ``resolve()`` exactly.
        """
        cache_key = f"cfg:{merchant_id}:{RESOLVED_CACHE_KEY}"

        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
                if cached is not None:
                    bag: dict[str, Any] = json.loads(cached)
                    return bag
            except Exception as e:  # Redis down / network blip → fall back to DB.
                logger.warning("config.cache.get_failed", key=cache_key, error=str(e))

        # 1. Merchant override bag (single query).
        overrides = (
            await self._session.execute(
                select(BotConfig.overrides).where(BotConfig.merchant_id == merchant_id)
            )
        ).scalar_one_or_none() or {}

        # 2. Resolve the merchant's tenant (single query).
        tenant_id = (
            await self._session.execute(
                select(Merchant.tenant_id).where(Merchant.id == merchant_id)
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            raise ValueError(f"Merchant {merchant_id} does not exist")

        # 3. Agency default template defaults (single query).
        defaults = (
            await self._session.execute(
                select(BotTemplate.defaults).where(
                    BotTemplate.tenant_id == tenant_id,
                    BotTemplate.is_default.is_(True),
                )
            )
        ).scalar_one_or_none() or {}

        resolved: dict[str, Any] = {}
        for key in ConfigKey:
            key_str = key.value
            value = _lookup(overrides, key_str)
            if value is None:
                value = _lookup(defaults, key_str)
            if value is None:
                value = SYSTEM_DEFAULTS.get(key)
            resolved[key_str] = value

        await self._cache(cache_key, resolved)
        return resolved

    async def invalidate(self, merchant_id: UUID, *, keys: list[str] | None = None) -> None:
        if self._redis is None:
            return
        try:
            if keys is None:
                pattern = f"cfg:{merchant_id}:*"
                async for raw in self._redis.scan_iter(match=pattern):
                    await self._redis.delete(raw)
            else:
                # Always drop the whole-bag entry alongside the targeted keys —
                # a single-key write still shifts the resolved bag.
                targets = [f"cfg:{merchant_id}:{k}" for k in keys]
                targets.append(f"cfg:{merchant_id}:{RESOLVED_CACHE_KEY}")
                await self._redis.delete(*targets)
        except Exception as e:  # never let a cache miss break a write.
            logger.warning(
                "config.cache.invalidate_failed", merchant_id=str(merchant_id), error=str(e)
            )

    async def _cache(self, key: str, value: Any) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(key, json.dumps(value), ex=CACHE_TTL_SECONDS)
        except Exception as e:
            logger.warning("config.cache.set_failed", key=key, error=str(e))


def _lookup(bag: dict[str, Any], dotted_key: str) -> Any:
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
