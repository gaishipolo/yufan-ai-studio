"""驱动配置的 mtime 指纹缓存：改 `.env` 下次请求即生效，无需重启。

## 为什么不用 `lru_cache`

`DriverSettings` 用 pydantic-settings 的 `env_file=(ENV_FILE,)`，**每次实例化都
整读 `.env` 并解析**。原来的 `@lru_cache(maxsize=1)` 把这次读取永久缓存住了 ——
改 `GRSAI_API_KEY` 必须重启服务（2026-11-23 那次空 key 事故的直接原因之一）。

## 缓存键

`(ENV_FILE 的 mtime_ns, size)` + **进程内环境变量快照指纹**。

- 文件没变 → 1 次 `stat()`，复用实例（成本与原 lru_cache 相当）
- 文件变了 → 重读 + 重建（下次请求生效）
- 环境变量变了（容器注入 / 测试 monkeypatch）→ 快照指纹变 → 重建
- 坏配置**不缓存**（否则坏值被粘住，改好了也不生效）

## 为什么不做成 TTL

TTL 的「最长延迟 N 秒」在**改 key 止血**场景里意味着继续用坏 key 提交 N 秒。
mtime 指纹是变更即生效，没有时间窗（对应 D3 拍板）。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from core.config import ENV_FILE

# ENV_FILE 在 core.config 里是字符串（测试可改），用 Path 包一层
_ENV_PATH = Path(ENV_FILE)

T = TypeVar("T")

# 缓存项：(mtime_ns, size, env 快照指纹) → 实例
_cache: dict[tuple[type, int, int, tuple[tuple[str, str], ...]], object] = {}


def _env_snapshot(env_prefix: str) -> tuple[tuple[str, str], ...]:
    """只取本供应商前缀的环境变量。

    排序保证指纹稳定 —— 否则 os.environ 的迭代顺序变化会误判为配置变更。
    取全量里与本供应商相关的部分：容器注入的 key 改了也要能感知到。
    """
    upper = env_prefix.upper()
    return tuple(
        sorted((k, v) for k, v in os.environ.items() if k.upper().startswith(upper))
    )


def _file_fingerprint() -> tuple[int, int]:
    try:
        stat = _ENV_PATH.stat()
    except OSError:
        # 没有 .env 文件（纯环境变量部署）：用固定指纹，靠 env 快照感知变化
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def vendor_settings(cls: type[T], *, env_prefix: str) -> Callable[[], T]:
    """生成一个「按 mtime 指纹缓存」的 settings getter。

    用法（驱动侧）：
        get_grsai_settings = vendor_settings(GrsaiSettings, env_prefix="GRSAI")

    返回的 getter 保持无参调用，与原 `@lru_cache` 版签名一致。
    """

    def getter() -> T:
        mtime, size = _file_fingerprint()
        env_snapshot = _env_snapshot(env_prefix)
        key = (cls, mtime, size, env_snapshot)
        cached = _cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        instance = cls()
        _cache[key] = instance
        return instance

    return getter


def clear_cache() -> None:
    """只给测试用：清掉全部缓存实例。"""
    _cache.clear()


__all__ = ["clear_cache", "vendor_settings"]
