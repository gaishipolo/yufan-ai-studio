"""数据库会话与引擎管理。

- 引擎按需惰性创建，测试可注入内存库
- `get_session` 作为 FastAPI 依赖，异常自动回滚
- 开发环境自动建表；生产请用 Alembic 迁移
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import get_settings
from core.logging import get_logger
from db.base import Base

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _engine_kwargs() -> dict[str, Any]:
    settings = get_settings()
    kwargs: dict[str, Any] = {"echo": settings.db_echo, "future": True}
    if settings.is_sqlite:
        # sqlite 不支持连接池参数；文件库需保证目录存在
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":///" in settings.db_url and ":memory:" not in settings.db_url:
            rel = settings.db_url.split("///", 1)[1]
            db_path = Path(rel)
            if not db_path.is_absolute():
                db_path = Path.cwd() / db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
        kwargs["pool_pre_ping"] = True
    return kwargs


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.db_url, **_engine_kwargs())
        logger.info("数据库引擎已创建: %s", settings.db_url.split("///")[0])
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：一次请求一个 session。

    提交由 service 层显式控制（避免隐式事务边界），异常时依赖负责回滚。
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db(*, drop: bool = False) -> None:
    """开发/测试用：按 ORM 元数据建表。生产环境请执行 Alembic 迁移。"""
    from db import registry

    registry.load_all_models()

    engine = get_engine()
    async with engine.begin() as conn:
        if drop:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据表已就绪（%d 张）", len(Base.metadata.tables))


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def reset_state_for_tests() -> None:
    """仅测试使用：清空全局单例，便于切换数据库 URL。"""
    global _engine, _session_factory
    _engine = None
    _session_factory = None
    os.environ.pop("SQL_ECHO", None)
