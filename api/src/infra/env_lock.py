"""`api/.env` 读-改-写的跨进程互斥锁。

LLM 配置页与模型运维页各自做「读整个 `.env` → 改几行 → 原子替换」。两个页面
（甚至两个后端进程）同时保存时，后写的会覆盖先写的改动 —— 原子写只保证不出现
半截文件，不保证两次读-改-写之间没有丢失更新。

这里用同目录的 `.env.lock` 做文件锁，锁的是 **整个读-改-写区间**，不是只锁
`os.replace`。锁文件独立于 `.env`：替换目标文件会让锁语义变得不可靠。

Windows 用 `msvcrt.locking`，POSIX 用 `fcntl.flock`；拿不到锁时最多等
`LOCK_TIMEOUT_SEC`，超时按 409 抛 `InvalidState`，不无限卡住请求。
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from core.config import ENV_FILE
from core.exceptions import InvalidState

_LOCK_PATH = Path(str(ENV_FILE) + ".lock")
_LOCK_TIMEOUT_SEC = 10.0
_POLL_SEC = 0.05


def _ensure_lock_byte(handle: BinaryIO) -> None:
    """Windows 的 LockFile 从当前文件位置锁 1 字节；空文件先写一个占位字节。"""
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()


def _try_acquire(handle: BinaryIO) -> bool:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
    except OSError:
        return False
    return True


def _release(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


@contextmanager
def env_write_lock() -> Iterator[None]:
    """持有 `.env` 写锁；超时抛 409（`InvalidState`）。"""
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOCK_PATH, "a+b") as handle:
        _ensure_lock_byte(handle)
        deadline = time.monotonic() + _LOCK_TIMEOUT_SEC
        while not _try_acquire(handle):
            if time.monotonic() >= deadline:
                raise InvalidState("配置文件正在被另一个页面/进程修改，请稍后重试")
            time.sleep(_POLL_SEC)
        try:
            yield
        finally:
            _release(handle)


__all__ = ["env_write_lock"]