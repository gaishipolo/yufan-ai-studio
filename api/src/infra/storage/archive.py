"""归档文件系统协议与实现；不包含素材分类、权限或生成业务。"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Protocol

from core.config import settings
from core.exceptions import BadRequest, Conflict, UpstreamError

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
    def root(self) -> Path: ...
    def resolve(self, key: str) -> Path: ...
    def read(self, path: Path) -> object: ...
    def write(self, path: Path, value: object) -> None: ...
    def copy(self, source: Path, destination: Path, digest: str | None = None) -> str: ...


class LocalArchiveStorage:
    """同步文件驱动；异步调用方必须在线程池内完成整次操作。"""

    def root(self) -> Path:
        path = Path(settings.media_archive_root)
        if not path.is_absolute() or not path.is_dir():
            raise UpstreamError("素材归档盘不可用，请检查 Z 盘连接；不会回退本地保存")
        return path.resolve()

    def resolve(self, key: str) -> Path:
        return within(self.root(), key)

    def read(self, path: Path) -> object:
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def copy(self, source: Path, destination: Path, digest: str | None = None) -> str:
        expected = digest or checksum(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if checksum(destination) != expected:
                raise Conflict("已有归档文件与本次输入不一致，禁止覆盖")
            return expected
        temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
        shutil.copyfile(source, temporary)
        if checksum(temporary) != expected:
            raise UpstreamError("归档复制校验失败，请检查共享盘连接")
        temporary.replace(destination)
        return expected


def within(base: Path, relative: str) -> Path:
    path = (base / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(base.resolve()):
        raise BadRequest("文件路径超出归档目录")
    return path


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def get_archive_storage() -> ArchiveStorage:
    return LocalArchiveStorage()
