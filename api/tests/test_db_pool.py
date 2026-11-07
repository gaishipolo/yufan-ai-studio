"""连接池的两件事：借不到连接要快速失败，且失败要说「过载」而不是「我写错了」。

为什么单独一个文件：这两条都不属于任何业务模块，它们是「服务忙起来之后会发生
什么」的答案。以前这个问题没有答案——池耗尽时请求安静地挂 30 秒，然后收到一个
500「数据库操作失败」，而代码里一个 bug 都没有。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.pool.impl import AsyncAdaptedQueuePool

from api.errors import register_exception_handlers


# --------------------------------------------------------------------------- #
# 池参数真的被用上了
# --------------------------------------------------------------------------- #
def test_file_db_gets_the_configured_pool() -> None:
    """sqlite 文件库也是 QueuePool：它同样会「一个慢请求拖住后面所有请求」。

    这里钉的是「配置里的池参数没有在 sqlite 分支被静默丢掉」。丢掉的表现不是
    报错，而是开发库用 `pool_size=5 / pool_timeout=30` 的默认值跑，
    和线上完全两套行为——池耗尽这类故障因此只能在线上第一次遇到。
    """
    from core.config import settings
    from db.session import _engine_kwargs, _is_memory_sqlite

    assert not _is_memory_sqlite(settings.db_url), "测试夹具应当是文件库"
    kwargs = _engine_kwargs()
    assert kwargs["pool_size"] == settings.db_pool_size
    assert kwargs["max_overflow"] == settings.db_max_overflow
    assert kwargs["pool_timeout"] == settings.db_pool_timeout
    assert kwargs["pool_recycle"] == settings.db_pool_recycle
    assert kwargs["pool_pre_ping"] is True


def test_pool_timeout_is_shorter_than_the_driver_default() -> None:
    """默认池超时必须明显短于 SQLAlchemy 的 30 秒。

    30 秒不是一个中性的默认值，它是「全站安静地卡住半分钟」的长度：没有日志、
    没有告警，只有用户那边转圈。
    """
    from core.config import Settings

    assert Settings().db_pool_timeout < 10


def test_memory_db_is_not_given_queue_pool_options() -> None:
    """内存库用 StaticPool，塞池参数会 `TypeError`——这里保证不会。"""
    from core.config import settings
    from db.session import _engine_kwargs

    original = settings.db_url
    try:
        settings.db_url = "sqlite+aiosqlite:///:memory:"
        kwargs = _engine_kwargs()
        assert "pool_size" not in kwargs
        assert "pool_timeout" not in kwargs
        # 建得起来才是真的没问题：TypeError 在 create_async_engine 里抛
        engine = create_async_engine(settings.db_url, **kwargs)
        assert isinstance(engine.pool, StaticPool)
        asyncio.run(engine.dispose())
    finally:
        settings.db_url = original


def test_file_db_pool_type_is_queue_pool() -> None:
    from core.config import settings
    from db.session import _engine_kwargs

    engine = create_async_engine(settings.db_url, **_engine_kwargs())
    try:
        assert isinstance(engine.pool, AsyncAdaptedQueuePool)
        assert engine.pool.size() == settings.db_pool_size
    finally:
        asyncio.run(engine.dispose())


# --------------------------------------------------------------------------- #
# 耗尽时是 503，且按配置的时长失败
# --------------------------------------------------------------------------- #
def _app_with_db_probe(pool_timeout: float) -> tuple[TestClient, Any]:
    """一个只有一条路由的小 app：它只需要借连接，不关心业务。

    刻意不复用 `client` 夹具：那是 session 级的共享 app，改它的池会让别的用例
    受影响；而且它的 `pool_timeout` 是 5 秒，为了验「快速失败」得真等 5 秒。
    """
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        # asyncio 引擎必须用 AsyncAdapted* 变体（同步的 QueuePool 会直接被拒）
        poolclass=AsyncAdaptedQueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=pool_timeout,
    )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/probe")
    async def probe() -> dict[str, str]:
        async with factory() as session:
            await session.execute(text("select 1"))
        return {"ok": "yes"}

    return TestClient(app, raise_server_exceptions=False), (engine, factory)


def test_pool_exhaustion_is_503_not_500() -> None:
    """借不到连接 → 503 + `retryAfter`，4xx/5xx 的语义不能被踩平。

    500 的潜台词是「我们写错了」，会把人叫起来查一段根本没有 bug 的代码；
    真正的处理是扩容、或者找出占着连接不放的那个慢查询。503 才说得出口。
    """
    client, (engine, factory) = _app_with_db_probe(pool_timeout=0.2)
    holder = None
    try:
        # 占住唯一那根连接：不提交、不关闭，池就再也借不出第二个
        holder = factory()
        asyncio.run(holder.execute(text("select 1")))

        response = client.get("/probe")

        assert response.status_code == 503, (
            f"池耗尽被映射成了 {response.status_code}：{response.text[:200]}"
        )
        body = response.json()
        assert body["code"] == 50_300, f"错误码不是 UNAVAILABLE：{body}"
        assert body["message"] == "服务繁忙，请稍后重试"
        assert body["details"]["reason"] == "db_pool_exhausted"
        assert body["details"]["retryAfter"] == 5
    finally:
        if holder is not None:
            asyncio.run(holder.close())
        asyncio.run(engine.dispose())


def test_pool_exhaustion_fails_fast() -> None:
    """等多久由 `db_pool_timeout` 决定，不是驱动的 30 秒默认值。"""
    import time

    client, (engine, factory) = _app_with_db_probe(pool_timeout=0.2)
    holder = None
    try:
        holder = factory()
        asyncio.run(holder.execute(text("select 1")))
        started = time.perf_counter()
        client.get("/probe")
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"池耗尽等了 {elapsed:.1f}s：超时没按配置走"
    finally:
        if holder is not None:
            asyncio.run(holder.close())
        asyncio.run(engine.dispose())


def test_pool_recovers_after_the_holder_lets_go() -> None:
    """占连接的人放手之后，下一个请求要正常——503 不能是粘住的。"""
    client, (engine, factory) = _app_with_db_probe(pool_timeout=0.2)
    holder = factory()
    try:
        asyncio.run(holder.execute(text("select 1")))
        assert client.get("/probe").status_code == 503

        asyncio.run(holder.close())
        assert client.get("/probe").status_code == 200
    finally:
        asyncio.run(engine.dispose())


def test_integrity_error_still_maps_to_409() -> None:
    """相邻分支没被新 handler 抢走：约束冲突仍然是 409。"""
    from sqlalchemy.exc import IntegrityError

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/conflict")
    async def conflict() -> None:
        raise IntegrityError("insert", {}, Exception("UNIQUE constraint failed"))

    response = TestClient(app, raise_server_exceptions=False).get("/conflict")
    assert response.status_code == 409
    assert response.json()["code"] == 40_900


def test_other_db_errors_are_still_500() -> None:
    """非池耗尽的数据库错误保持 500：把它们一起改成 503 会掩盖真的 bug。"""
    from sqlalchemy.exc import OperationalError

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/broken")
    async def broken() -> None:
        raise OperationalError("select 1", {}, Exception("no such table"))

    response = TestClient(app, raise_server_exceptions=False).get("/broken")
    assert response.status_code == 500
    assert response.json()["code"] == 50_001


def test_env_example_documents_the_pool_knobs(tmp_path: Path) -> None:
    """新增配置项必须出现在 `.env.example` 里。

    不存在这个文件里的配置项，等于「只有读过源码的人知道他还能调」。
    """
    env = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    for name in ("DB_POOL_TIMEOUT", "DB_POOL_RECYCLE", "DB_STATEMENT_TIMEOUT_MS"):
        assert name in env, f".env.example 里没有 {name}"


def test_postgres_gets_the_statement_timeout() -> None:
    """PG 上语句超时必须真的传到连接参数里，否则那项配置只是装饰。

    没有真 PG 可连，所以这里只钉「配置 → 引擎参数」这一段。**没有钉住的是**
    「PG 真的会按这个值中断语句」——那需要真实数据库，属 T30 观察期。
    但至少「改了配置忘了在 session.py 里用上」这种漏配不会发生。
    """
    from core.config import settings
    from db.session import _engine_kwargs

    original = (settings.db_url, settings.db_statement_timeout_ms)
    try:
        settings.db_url = "postgresql+asyncpg://u:p@localhost:5432/app"
        settings.db_statement_timeout_ms = 30000
        kwargs = _engine_kwargs()
        assert kwargs["connect_args"]["server_settings"]["statement_timeout"] == "30000"
        assert kwargs["pool_timeout"] == settings.db_pool_timeout
        assert kwargs["pool_recycle"] == settings.db_pool_recycle
    finally:
        settings.db_url, settings.db_statement_timeout_ms = original


def test_disabled_statement_timeout_is_not_sent() -> None:
    """0 表示「不设」：传 `statement_timeout=0` 在 PG 上等于**永不超时**，
    与预期相反。所以 0 的时候一个参数都不该发。"""
    from core.config import settings
    from db.session import _engine_kwargs

    original = (settings.db_url, settings.db_statement_timeout_ms)
    try:
        settings.db_url = "postgresql+asyncpg://u:p@localhost:5432/app"
        settings.db_statement_timeout_ms = 0
        assert "connect_args" not in _engine_kwargs()
    finally:
        settings.db_url, settings.db_statement_timeout_ms = original


@pytest.mark.parametrize("field", ["db_pool_timeout", "db_pool_recycle", "db_statement_timeout_ms"])
def test_new_settings_have_types(field: str) -> None:
    from core.config import Settings

    value = getattr(Settings(), field)
    assert isinstance(value, int | float)
