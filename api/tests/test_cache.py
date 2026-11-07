"""缓存驱动的行为测试。

重点不是「能存能取」，而是三条容易被实现写错的语义：
TTL 真的会过期、`incr` 不会把窗口续期、超限时是淘汰而不是无限增长。
"""
from __future__ import annotations

import asyncio

import pytest

from infra.cache.base import cache_get_json, cache_key, cache_set_json
from infra.cache.memory import CacheSettings, MemoryCache


def _cache(**kwargs: object) -> MemoryCache:
    return MemoryCache(CacheSettings(**kwargs))  # type: ignore[arg-type]


def test_set_get_roundtrip() -> None:
    cache = _cache()
    asyncio.run(cache.set("k", b"value"))
    assert asyncio.run(cache.get("k")) == b"value"
    assert asyncio.run(cache.exists("k")) is True


def test_missing_key_returns_none_not_error() -> None:
    cache = _cache()
    assert asyncio.run(cache.get("absent")) is None
    assert asyncio.run(cache.exists("absent")) is False


def test_ttl_expires() -> None:
    cache = _cache()

    async def scenario() -> None:
        await cache.set("k", b"v", ttl=0.05)
        assert await cache.get("k") == b"v"
        await asyncio.sleep(0.08)
        assert await cache.get("k") is None
        assert await cache.exists("k") is False

    asyncio.run(scenario())


def test_non_positive_ttl_deletes_immediately() -> None:
    cache = _cache()
    asyncio.run(cache.set("k", b"v"))
    asyncio.run(cache.set("k", b"v2", ttl=0))
    # 语义是「写入即过期」，不是「永久」——否则调用方算错 ttl 会得到相反的后果
    assert asyncio.run(cache.get("k")) is None


def test_value_must_be_bytes() -> None:
    cache = _cache()

    async def scenario() -> None:
        with pytest.raises(TypeError, match="bytes"):
            await cache.set("k", "str")  # type: ignore[arg-type]

    asyncio.run(scenario())


def test_incr_starts_from_zero() -> None:
    cache = _cache()
    assert asyncio.run(cache.incr("counter")) == 1
    assert asyncio.run(cache.incr("counter", 4)) == 5


def test_incr_keeps_original_expiry() -> None:
    """每次自增都续期，会让「每分钟 100 次」变成永不归零的计数器。"""
    cache = _cache()

    async def scenario() -> None:
        await cache.incr("window", ttl=0.05)
        before = cache._data["window"][1]  # 白盒：断言过期时间没被改写
        await cache.incr("window")
        assert cache._data["window"][1] == before
        await asyncio.sleep(0.08)
        assert await cache.get("window") is None

    asyncio.run(scenario())


def test_incr_on_non_integer_raises() -> None:
    cache = _cache()

    async def scenario() -> None:
        await cache.set("k", b"abc")
        with pytest.raises(ValueError, match="不是整数"):
            await cache.incr("k")

    asyncio.run(scenario())


def test_lru_eviction_respects_limit() -> None:
    cache = _cache(max_entries=2)

    async def scenario() -> None:
        await cache.set("a", b"1")
        await cache.set("b", b"2")
        await cache.get("a")  # 让 a 变成最近使用
        await cache.set("c", b"3")
        # b 最久未用，被淘汰；a 因为刚读过而保留
        assert await cache.get("b") is None
        assert await cache.get("a") == b"1"
        assert await cache.get("c") == b"3"
        assert cache.size == 2

    asyncio.run(scenario())


def test_clear_empties_namespace() -> None:
    cache = _cache()

    async def scenario() -> None:
        await cache.set("a", b"1")
        await cache.clear()
        assert await cache.get("a") is None

    asyncio.run(scenario())


def test_cache_key_uses_prefix_and_rejects_colon() -> None:
    key = cache_key("model", "list")
    assert key.endswith("model:list")
    assert key.count(":") >= 2  # 前缀 + 两段
    with pytest.raises(ValueError, match="冒号"):
        cache_key("limit", "a:b")
    with pytest.raises(ValueError, match="不能为空"):
        cache_key("")


def test_json_helpers_roundtrip() -> None:
    cache = _cache()

    async def scenario() -> None:
        await cache_set_json(cache, "j", {"名字": "值", "n": 1}, ttl=10)
        assert await cache_get_json(cache, "j") == {"名字": "值", "n": 1}

    asyncio.run(scenario())


def test_json_helper_drops_corrupted_value() -> None:
    """损坏只可能来自旧版本结构或手工改值，回源重建是正确的，不该抛 500。"""
    cache = _cache()

    async def scenario() -> None:
        await cache.set("bad", b"{not json")
        assert await cache_get_json(cache, "bad") is None
        assert await cache.get("bad") is None

    asyncio.run(scenario())


def test_driver_symbol_is_loadable() -> None:
    """`Driver` 必须是可无参实例化的类，否则 `load_driver` 会在启动期报错。"""
    from infra.cache.memory import Driver

    instance = Driver()
    assert isinstance(instance, MemoryCache)
