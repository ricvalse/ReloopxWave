"""RateLimitMiddleware: limits only public prefixes, 429s over the cap, and
fails open when Redis misbehaves."""

from __future__ import annotations

from types import SimpleNamespace

from starlette.responses import PlainTextResponse

from api.core.middleware import RateLimitMiddleware, client_ip, rate_limit_key


def _request(path: str, *, redis, ip: str = "9.9.9.9"):
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        headers={"x-forwarded-for": ip},
        client=SimpleNamespace(host=ip),
        app=SimpleNamespace(state=SimpleNamespace(arq=redis)),
    )


# Starlette Headers is a Mapping; SimpleNamespace dict.get matches the .get usage.
class _Headers(dict):
    pass


def _req(path, redis, ip="9.9.9.9"):
    r = _request(path, redis=redis, ip=ip)
    r.headers = _Headers({"x-forwarded-for": ip})
    return r


class FakeRedis:
    def __init__(self, *, start=0, fail=False):
        self.value = start
        self.fail = fail
        self.expired = False

    async def incr(self, key):
        if self.fail:
            raise RuntimeError("redis down")
        self.value += 1
        return self.value

    async def expire(self, key, ttl):
        self.expired = True


async def _call_next(_request):
    return PlainTextResponse("ok")


def test_helpers() -> None:
    assert rate_limit_key("1.2.3.4", now=1784073600.0).startswith("rl:public:1.2.3.4:")
    r = _req("/webhooks/whatsapp", FakeRedis())
    assert client_ip(r) == "9.9.9.9"


async def test_non_public_path_passes_through() -> None:
    redis = FakeRedis(start=999)
    mw = RateLimitMiddleware(None, limit_per_min=1, prefixes=("/webhooks",))
    resp = await mw.dispatch(_req("/merchants", redis), _call_next)
    assert resp.status_code == 200  # not rate-limited; redis untouched
    assert redis.value == 999


async def test_under_limit_allows_then_blocks() -> None:
    redis = FakeRedis(start=0)
    mw = RateLimitMiddleware(None, limit_per_min=2, prefixes=("/webhooks",))
    r1 = await mw.dispatch(_req("/webhooks/x", redis), _call_next)
    r2 = await mw.dispatch(_req("/webhooks/x", redis), _call_next)
    r3 = await mw.dispatch(_req("/webhooks/x", redis), _call_next)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429  # third request over the cap of 2


async def test_fails_open_on_redis_error() -> None:
    redis = FakeRedis(fail=True)
    mw = RateLimitMiddleware(None, limit_per_min=1, prefixes=("/webhooks",))
    resp = await mw.dispatch(_req("/webhooks/x", redis), _call_next)
    assert resp.status_code == 200  # redis down → let it through


async def test_disabled_when_limit_zero() -> None:
    redis = FakeRedis(start=999)
    mw = RateLimitMiddleware(None, limit_per_min=0, prefixes=("/webhooks",))
    resp = await mw.dispatch(_req("/webhooks/x", redis), _call_next)
    assert resp.status_code == 200
    assert redis.value == 999
