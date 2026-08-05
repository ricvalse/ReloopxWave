from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config_resolver.schema import SYSTEM_DEFAULTS, ConfigKey
from db.models import BotConfig, BotTemplate, ConversationProfile, Merchant
from shared import get_logger

logger = get_logger(__name__)

CACHE_TTL_SECONDS = 60

# Suffix for the whole-bag cache entry written by `resolve_all`. Namespaced
# per merchant exactly like the per-key entries (`cfg:{merchant_id}:{key}`),
# so the `cfg:{merchant_id}:*` invalidation scan covers it too.
# Il suffisso è versionato perché il bag cachato è una fotografia dell'insieme
# di `ConfigKey` esistenti al momento della scrittura. Quando una chiave viene
# **rimossa**, un bag vecchio letto dalla cache la contiene ancora, e
# `BotConfigSchema` (che ha `extra="forbid"` e viene applicato anche in lettura su
# `GET /bot-config/{id}/resolved`) fallirebbe la validazione finché la voce non
# scade. Bumpare questo suffisso rende i bag vecchi irraggiungibili: scadono da
# soli senza essere mai più letti. Da alzare a ogni rimozione di chiave.
# v2: rimozione delle chiavi `no_answer.*` (ADR 0024).
# v3: rimozione di `schedule.active_hours`, sostituita da `schedule.mode` +
#     `schedule.weekly` (orari di risposta). Senza il bump, per l'intera durata
#     della cache il pannello di ogni merchant che aveva toccato quel campo
#     risponderebbe 500 invece di mostrare gli orari nuovi.
RESOLVED_CACHE_KEY = "__resolved_v3__"

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
    """Cascata: profilo → merchant → agenzia → system default.

    I primi tre livelli sono override-bag della stessa forma, il quarto sono i
    default in codice. Il livello **profilo** (ADR 0022) è per-conversazione e
    opzionale: con ``profile_id=None`` — il default di ogni firma — la cascata è
    identica a quella a tre livelli di prima, quindi i ~40 call-site esistenti
    non cambiano comportamento.

    Every lookup round-trips through Redis with a short TTL. Cache invalidation
    happens on write at the matching level. The TTL is a safety net, not the
    primary correctness mechanism — every Redis op is best-effort and degrades
    to a direct DB read if Redis is unreachable.
    """

    def __init__(self, session: AsyncSession, redis: Redis | None = None) -> None:
        self._session = session
        self._redis = redis if redis is not None else _shared_redis

    def _cache_prefix(self, merchant_id: UUID, profile_id: UUID | None) -> str:
        """Namespace di cache per merchant, con il profilo quando c'è.

        Senza il segmento del profilo due conversazioni dello stesso merchant su
        profili diversi si sovrascriverebbero a vicenda la stessa chiave — è la
        collisione che ADR 0022 segnalava fra le conseguenze. Resta sotto
        ``cfg:{merchant_id}:`` così l'invalidazione a scan per merchant continua
        a coprirlo senza modifiche.
        """
        if profile_id is None:
            return f"cfg:{merchant_id}"
        return f"cfg:{merchant_id}:p:{profile_id}"

    async def _profile_overrides(self, profile_id: UUID | None) -> dict[str, Any]:
        """Override del profilo, o ``{}`` se assente/disabilitato/cancellato.

        Degradare a ``{}`` invece di sollevare è deliberato: un profilo rimosso
        mentre una conversazione lo sta usando fa tornare quella conversazione al
        comportamento del merchant, non rompe il turno.
        """
        if profile_id is None:
            return {}
        profile = await self._session.get(ConversationProfile, profile_id)
        if profile is None or not profile.enabled:
            return {}
        return dict(profile.overrides or {})

    async def resolve(
        self, key: ConfigKey | str, *, merchant_id: UUID, profile_id: UUID | None = None
    ) -> Any:
        key_str = key.value if isinstance(key, ConfigKey) else key
        cache_key = f"{self._cache_prefix(merchant_id, profile_id)}:{key_str}"

        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
                if cached is not None:
                    return json.loads(cached)
            except Exception as e:  # Redis down / network blip → fall back to DB.
                logger.warning("config.cache.get_failed", key=cache_key, error=str(e))

        # 0. Profilo di conversazione (DELTA sopra il merchant, ADR 0022)
        if profile_id is not None:
            value = _lookup(await self._profile_overrides(profile_id), key_str)
            if value is not None:
                await self._cache(cache_key, value)
                return value

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

        # 2. Agency default (resolve the merchant's tenant first, then pick the template:
        #    prefer the merchant's specific template_id if linked, else the tenant default)
        tenant_id_row = await self._session.execute(
            select(Merchant.tenant_id).where(Merchant.id == merchant_id)
        )
        tenant_id = tenant_id_row.scalar_one_or_none()
        if tenant_id is None:
            raise ValueError(f"Merchant {merchant_id} does not exist")

        specific_template_id = bot_cfg.template_id if bot_cfg is not None else None
        if specific_template_id is not None:
            template = await self._session.get(BotTemplate, specific_template_id)
        else:
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

    async def resolve_all(
        self, *, merchant_id: UUID, profile_id: UUID | None = None
    ) -> dict[str, Any]:
        """Resolve every ``ConfigKey`` for a merchant in a single pass.

        One Redis read of the whole bag (``cfg:{merchant_id}:__resolved__``) on
        a hit, or ≤3 DB queries on a miss — versus one ``resolve()`` round-trip
        per key (58 keys → up to ~174 queries cold). Returns a flat dict keyed
        by ``ConfigKey.value`` (dotted), ready to feed ``_dotted_set``. Cascade
        and None-skip semantics match ``resolve()`` exactly.
        """
        cache_key = f"{self._cache_prefix(merchant_id, profile_id)}:{RESOLVED_CACHE_KEY}"

        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
                if cached is not None:
                    bag: dict[str, Any] = json.loads(cached)
                    return bag
            except Exception as e:  # Redis down / network blip → fall back to DB.
                logger.warning("config.cache.get_failed", key=cache_key, error=str(e))

        # 1. Merchant override bag + template link (single query).
        bot_cfg_row = (
            await self._session.execute(
                select(BotConfig).where(BotConfig.merchant_id == merchant_id)
            )
        ).scalar_one_or_none()
        overrides = dict(bot_cfg_row.overrides or {}) if bot_cfg_row else {}
        specific_template_id = bot_cfg_row.template_id if bot_cfg_row is not None else None

        # 2. Resolve the merchant's tenant (single query).
        tenant_id = (
            await self._session.execute(
                select(Merchant.tenant_id).where(Merchant.id == merchant_id)
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            raise ValueError(f"Merchant {merchant_id} does not exist")

        # 3. Agency defaults — prefer merchant's linked template, fall back to tenant default.
        if specific_template_id is not None:
            tmpl_row = await self._session.get(BotTemplate, specific_template_id)
            defaults = dict(tmpl_row.defaults or {}) if tmpl_row else {}
        else:
            defaults = (
                await self._session.execute(
                    select(BotTemplate.defaults).where(
                        BotTemplate.tenant_id == tenant_id,
                        BotTemplate.is_default.is_(True),
                    )
                )
            ).scalar_one_or_none() or {}

        # 0. Profilo di conversazione — livello più alto della cascata.
        profile_overrides = await self._profile_overrides(profile_id)

        resolved: dict[str, Any] = {}
        for key in ConfigKey:
            key_str = key.value
            value = _lookup(profile_overrides, key_str)
            if value is None:
                value = _lookup(overrides, key_str)
            if value is None:
                value = _lookup(defaults, key_str)
            if value is None:
                value = SYSTEM_DEFAULTS.get(key)
            resolved[key_str] = value

        await self._cache(cache_key, resolved)
        return resolved

    async def invalidate(self, merchant_id: UUID, *, keys: list[str] | None = None) -> None:
        """Invalida la cache del merchant.

        ATTENZIONE: la forma mirata (``keys=[...]``) colpisce solo le chiavi
        senza profilo. Chi scrive un profilo — o qualunque cosa che i profili
        possano sovrascrivere — deve chiamare con ``keys=None``, che fa lo scan
        di ``cfg:{merchant_id}:*`` e porta via anche le voci namespacate per
        profilo.
        """
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
    """Resolve a dotted key (`a.b.c`) from a config bag.

    Primary shape is a NESTED bag (`{"a": {"b": {"c": v}}}`) — the convention
    written by `_dotted_set` in the bot-config router. As a backward-compatible
    fallback we also accept the FLAT shape (`{"a.b.c": v}`): some write paths
    stored the whole dotted string as a single top-level key (notably the
    playground `/apply` endpoint, which wrote `bot.system_prompt_additions`
    flat), and the nested walk alone would never find those — the value was
    silently dropped from the resolved config. Nested wins when both exist.
    """
    node: Any = bag
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            # Nested walk missed — fall back to the flat dotted key, if present.
            return bag.get(dotted_key)
        node = node[part]
    return node


async def resolve(
    session: AsyncSession,
    redis: Redis | None,
    *,
    merchant_id: UUID,
    key: ConfigKey | str,
    profile_id: UUID | None = None,
) -> Any:
    return await ConfigResolver(session, redis).resolve(
        key, merchant_id=merchant_id, profile_id=profile_id
    )
