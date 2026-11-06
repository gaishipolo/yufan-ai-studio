"""归档存储协议；不含素材分类、权限或生成业务。

`infra/` 里每个「可替换实现」都是一个 kind（storage / queue / ai / archive），
约定是 `infra/<kind>/<name>.py` 暴露 `Driver`，`<kind>_driver` 选它。
本文件只放协议与跨实现共用的文件语义，具体实现见 `local.py`。
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Protocol

from core.exceptions import BadRequest, Conflict

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


def acquire(stream: IO[bytes]) -> None:
    stream.seek(0, 2)
    if not stream.tell():
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    if sys.platform == "win32":
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def release(stream: IO[bytes]) -> None:
    stream.seek(0)
    if sys.platform == "win32":
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def lease(path: Path) -> Iterator[None]:
    """OS 锁随进程退出自动释放；文件存在不代表仍被占用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        try:
            acquire(stream)
        except OSError as exc:
            raise Conflict("归档正在被其他执行器处理，请稍后继续") from exc
        try:
            yield
        finally:
            release(stream)


class ArchiveStorage(Protocol):
    """归档盘最小接口。

    **已知边界**：接口以 `Path` 表达位置，所以实现必须是「有本地路径语义」的存储
    （本地目录、SMB/UNC、挂载点的对象存储网关）。要接纯 HTTP 的 S3/MinIO，
    得先在协议层把 `resolve() -> Path` 换成句柄，那是另一件事，别在驱动里偷偷绕。
    """

    def root(self) -> Path: ...
    def resolve(self, key: str) -> Path: ...
    def read(self, path: Path) -> object: ...
    def write(self, path: Path, value: object) -> None: ...
    def copy(self, source: Path, destination: Path, digest: str | None = None) -> str: ...


def within(base: Path, relative: str) -> Path:
    path = (base / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(base.resolve()):
        raise BadRequest("文件路径超出归档目录")
    return path


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
