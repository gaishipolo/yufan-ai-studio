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

import hashlib
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

#: 缓存条目上限。键里含 env 快照，所以**每次 env 变化都会新增一条键**。
#: 正常部署 env 长期不变（只有 1 条），但密钥轮转 / 容器注入频繁变动时会累积。
#: 超过上限就整体清空 —— 重建一次配置的成本远低于让缓存无限增长。
_MAX_CACHE_ENTRIES = 32


def _env_snapshot(env_prefixes: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """只取本供应商前缀的环境变量。

    排序保证指纹稳定 —— 否则 os.environ 的迭代顺序变化会误判为配置变更。
    取全量里与本供应商相关的部分：容器注入的 key 改了也要能感知到。

    前缀可以有多个：供应商的参数偶尔带**旧名**（不带本供应商前缀），
    那个名字也要进快照，否则改它不生效。
    """
    uppers = tuple(prefix.upper() for prefix in env_prefixes)
    return tuple(
        sorted(
            (k, v)
            for k, v in os.environ.items()
            if any(k.upper().startswith(u) for u in uppers)
        )
    )


def _file_fingerprint() -> tuple[int, int]:
    try:
        stat = _ENV_PATH.stat()
    except OSError:
        # 没有 .env 文件（纯环境变量部署）：用固定指纹，靠 env 快照感知变化
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def vendor_settings(
    cls: type[T], *, env_prefix: str | tuple[str, ...]
) -> Callable[[], T]:
    """生成一个「按 mtime 指纹缓存」的 settings getter。

    用法（驱动侧）：
        get_grsai_settings = vendor_settings(GrsaiSettings, env_prefix="GRSAI")
        # 参数带旧名时把旧名也列进来（改它要能立刻生效）：
        get_libtv_settings = vendor_settings(
            LibTvSettings, env_prefix=("LIBTV", "NODE_BINARY")
        )

    返回的 getter 保持无参调用，与原 `@lru_cache` 版签名一致。
    """
    prefixes = (env_prefix,) if isinstance(env_prefix, str) else tuple(env_prefix)

    def getter() -> T:
        mtime, size = _file_fingerprint()
        env_snapshot = _env_snapshot(prefixes)
        key = (cls, mtime, size, env_snapshot)
        cached = _cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        if len(_cache) >= _MAX_CACHE_ENTRIES:
            _cache.clear()
        instance = cls()
        _cache[key] = instance
        return instance

    return getter


def clear_cache() -> None:
    """只给测试用：清掉全部缓存实例。"""
    _cache.clear()


#: 敏感字段名（子串匹配，忽略大小写）。这些字段的**值**不进指纹明文。
_SENSITIVE_HINTS = ("key", "secret", "token", "password", "passwd", "credential", "cookie")


def _field_digest(name: str, value: object) -> str:
    """字段的指纹片段。

    敏感字段用**值的哈希**而不是明文：指纹会进日志、进巡检报告，不能带密钥；
    但密钥轮转也应该被指纹反映出来（否则运维看不出「配置换了一版」）。
    """
    text = str(value)
    if any(hint in name.lower() for hint in _SENSITIVE_HINTS):
        text = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{name}={text}"


def config_fingerprint(cls: type) -> str:
    """当前生效配置的指纹（**不含密钥明文**）。

    给 `app doctor` 用：运维需要知道「现在生效的是哪一版配置」，但指纹不能
    出现密钥。取实例字段（名 + 值）排序后 sha256，取前 12 位。

    配置本身是坏的（缺必填）时返回 ``"invalid"`` —— 巡检要能一眼看出「这版
    配置没读出来」，而不是把坏配置算成一个看起来正常的指纹。
    """
    try:
        instance = cls()
    except Exception:  # noqa: BLE001 - 坏配置也要能给出指纹
        return "invalid"
    fields = type(instance).model_fields
    parts = sorted(
        _field_digest(name, getattr(instance, name, "")) for name in fields
    )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]


__all__ = ["clear_cache", "config_fingerprint", "vendor_settings"]
