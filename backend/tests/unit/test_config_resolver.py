"""Unit tests for the config cascade resolver — `resolve_all` + cache busting.

No DB, no Redis: the session and Redis are tiny fakes so the cascade precedence
(merchant override > agency default > system default) and the whole-bag cache
behaviour are verified in isolation.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from config_resolver.resolver import RESOLVED_CACHE_KEY, ConfigResolver, _lookup
from config_resolver.schema import SYSTEM_DEFAULTS, ConfigKey


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class FakeBotConfig:
    """Minimal stand-in for a BotConfig ORM row returned by the first execute()."""

    def __init__(self, overrides: dict[str, Any], template_id: Any = None) -> None:
        self.overrides = overrides
        self.template_id = template_id


class FakeSession:
    """Returns canned scalar results in FIFO order, one per `execute()`."""

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.calls = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> _Result:
        self.calls += 1
        return _Result(self._results.pop(0))


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self.store.pop(k, None)

    async def scan_iter(self, match: str | None = None):  # type: ignore[no-untyped-def]
        prefix = match.rstrip("*") if match else ""
        for k in list(self.store.keys()):
            if k.startswith(prefix):
                yield k


async def test_resolve_all_cascade_precedence() -> None:
    merchant_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    overrides = {"scoring": {"hot_threshold": 90}}
    defaults = {"scoring": {"hot_threshold": 70, "cold_threshold": 20}}
    session = FakeSession([FakeBotConfig(overrides), tenant_id, defaults])
    resolver = ConfigResolver(session, redis=None)  # type: ignore[arg-type]

    flat = await resolver.resolve_all(merchant_id=merchant_id)

    # merchant override wins over template default + system default
    assert flat["scoring.hot_threshold"] == 90
    # template default wins over system default
    assert flat["scoring.cold_threshold"] == 20
    # system default where nothing is set anywhere
    assert flat["bot.formality"] == SYSTEM_DEFAULTS[ConfigKey.BOT_FORMALITY]
    # exactly 3 DB queries regardless of key count
    assert session.calls == 3


async def test_resolve_all_missing_merchant_raises() -> None:
    session = FakeSession([None, None])  # no overrides, no tenant
    resolver = ConfigResolver(session, redis=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await resolver.resolve_all(merchant_id=uuid.uuid4())


async def test_resolve_all_uses_and_writes_cache() -> None:
    merchant_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    redis = FakeRedis()
    session = FakeSession([FakeBotConfig({}), tenant_id, {}])
    resolver = ConfigResolver(session, redis=redis)  # type: ignore[arg-type]

    first = await resolver.resolve_all(merchant_id=merchant_id)
    assert session.calls == 3
    cache_key = f"cfg:{merchant_id}:{RESOLVED_CACHE_KEY}"
    assert cache_key in redis.store
    assert json.loads(redis.store[cache_key]) == first

    # Second call is served from cache — no further DB queries.
    second = await resolver.resolve_all(merchant_id=merchant_id)
    assert second == first
    assert session.calls == 3


async def test_invalidate_targeted_keys_also_drops_resolved_bag() -> None:
    merchant_id = uuid.uuid4()
    redis = FakeRedis()
    resolved_key = f"cfg:{merchant_id}:{RESOLVED_CACHE_KEY}"
    per_key = f"cfg:{merchant_id}:scoring.hot_threshold"
    redis.store[resolved_key] = json.dumps({"x": 1})
    redis.store[per_key] = json.dumps(90)

    resolver = ConfigResolver(FakeSession([]), redis=redis)  # type: ignore[arg-type]
    await resolver.invalidate(merchant_id, keys=["scoring.hot_threshold"])

    assert per_key not in redis.store
    # the whole-bag entry must be dropped even though it wasn't listed
    assert resolved_key not in redis.store


async def test_invalidate_all_drops_everything_for_merchant() -> None:
    merchant_id = uuid.uuid4()
    other = uuid.uuid4()
    redis = FakeRedis()
    redis.store[f"cfg:{merchant_id}:{RESOLVED_CACHE_KEY}"] = "{}"
    redis.store[f"cfg:{merchant_id}:bot.tone"] = '"x"'
    redis.store[f"cfg:{other}:bot.tone"] = '"y"'  # different merchant — keep

    resolver = ConfigResolver(FakeSession([]), redis=redis)  # type: ignore[arg-type]
    await resolver.invalidate(merchant_id)

    assert not any(k.startswith(f"cfg:{merchant_id}:") for k in redis.store)
    assert f"cfg:{other}:bot.tone" in redis.store


# ---- _lookup: nested primary shape + flat-key backward-compat fallback -------


def test_lookup_nested_shape() -> None:
    bag = {"business": {"name": "Acme"}}
    assert _lookup(bag, "business.name") == "Acme"
    assert _lookup(bag, "business.missing") is None
    assert _lookup(bag, "nope.at.all") is None


def test_lookup_flat_key_fallback() -> None:
    # Some write paths (playground `/apply`) stored the whole dotted string as a
    # single top-level key. The nested walk misses it; the flat fallback finds it.
    bag = {"bot.system_prompt_additions": "usa sempre il tu"}
    assert _lookup(bag, "bot.system_prompt_additions") == "usa sempre il tu"


def test_lookup_nested_wins_over_flat_when_both_present() -> None:
    bag = {"bot": {"system_prompt_additions": "nested"}, "bot.system_prompt_additions": "flat"}
    assert _lookup(bag, "bot.system_prompt_additions") == "nested"


def test_lookup_returns_nested_none_value_without_flat_fallback() -> None:
    # A key genuinely present nested with a None value resolves to None (it is set).
    bag = {"bot": {"first_message": None}}
    assert _lookup(bag, "bot.first_message") is None


async def test_resolve_reads_flat_stored_dotted_key() -> None:
    # Regression: a flat-stored override (as `/apply` writes it) must resolve —
    # previously the nested-only walk dropped it, silently ignoring the value.
    merchant_id = uuid.uuid4()
    overrides = {"bot.system_prompt_additions": "regola del tester"}
    session = FakeSession([FakeBotConfig(overrides)])
    resolver = ConfigResolver(session, redis=None)  # type: ignore[arg-type]

    value = await resolver.resolve(ConfigKey.BOT_SYSTEM_PROMPT_ADDITIONS, merchant_id=merchant_id)
    assert value == "regola del tester"
    # Resolved at the merchant level → a single DB query, no tenant/template lookup.
    assert session.calls == 1


def test_rag_min_score_system_default_is_reachable() -> None:
    # 0.7 was unreachable by raw-query cosine on text-embedding-3-small (~0.66),
    # so every retrieval came back empty. Guard the lowered default.
    assert SYSTEM_DEFAULTS[ConfigKey.RAG_MIN_SCORE] == 0.6
