"""归档存储协议；不含素材分类、权限或生成业务。

`infra/` 里每个「可替换实现」都是一个 kind（storage / queue / ai / archive），
约定是 `infra/<kind>/<name>.py` 暴露 `Driver`，`<kind>_driver` 选它。
本文件只放协议与跨实现共用的文件语义，具体实现见 `local.py`。
"""

from __future__ import annotations

import errno
import hashlib
import json
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from pathlib import Path
from typing import IO, Protocol, TypeAlias

from core.exceptions import AppError, BadRequest, Conflict

# 共享盘上原子替换暴露的瞬时缺失：3 次约 150ms 足以跨过桥接层的一次重写。
FILE_READ_RETRIES = 3
# Windows/SMB 的短暂文件占用：复用同一临时文件，最多等待 50+100+200ms。
FILE_REPLACE_RETRIES = 4

# 归档位置：`str` 是存储无关的 key（新代码一律用它），`Path` 只为兼容既有调用点。
# 本地驱动两者等价（Path 会被折成相对 key），纯 HTTP 驱动只认 str。
Location: TypeAlias = str | Path

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


ARCHIVE_BUSY = "归档正在被其他执行器处理，请稍后继续"


@contextmanager
def lease(
    path: Path,
    *,
    message: str = ARCHIVE_BUSY,
    error: type[AppError] = Conflict,
) -> Iterator[None]:
    """OS 锁随进程退出自动释放；文件存在不代表仍被占用。

    `error` / `message` 由调用方指定，因为这里抢的可能是完全不同的三把锁：
    共享归档、单个任务档案、账号执行器。三者被占用时的处理方式不一样
    （归档要等、任务档案要核对、账号执行器要排队），统一报「归档正被处理」
    只会把人引到错误的排查方向。

    打不开锁文件（权限、被别的进程独占共享）与「锁已被别人持有」是两件事，
    但都不该冒泡成 500：它们是**可预期的忙**，不是服务端故障。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stream = path.open("a+b")
    except OSError as exc:
        raise error(message) from exc
    with stream:
        try:
            acquire(stream)
        except OSError as exc:
            raise error(message) from exc
        try:
            yield
        finally:
            release(stream)


class ArchiveStorage(Protocol):
    """归档存储最小接口。

    **位置用 key 表达，不用本地路径。** 早期版本这里是 `resolve() -> Path`，
    等于把「实现必须有本地路径语义」写进了协议（本地目录、SMB、挂载点网关才行，
    纯 HTTP 的 S3/MinIO 接不进来）。现在分成两类方法：

    - **元数据**：`read` / `write` 收 key，驱动自己决定怎么落（本地 JSON 文件、
      对象存储的一个小对象、以后换成 DB 也行）。
    - **字节**：需要真实文件才能交给 ffmpeg / 桥接层 / 上游 SDK，用
      `materialize(key)` 拿一个**只读的本地路径**，用完即弃。本地驱动直接交出
      原路径（零拷贝），远端驱动下到本地缓存再交出。

    内容寻址的约定：素材字节的 key 就是它的 sha256（见 `infra/archive/blobs.py`）。
    于是「字节会不会变」不再是需要检测的问题——同一个 key 永远指同一份字节，
    变了就是另一个 key。这条约定取代了原先「发布前重算 sha256 发现被改」的做法。
    """

    def root(self) -> Path:
        """本地工作根目录；仅本地类驱动可用，远端驱动应抛 `UpstreamError`。"""
        ...

    def resolve(self, key: str) -> Path:
        """key → 本地路径。**只对本地类驱动成立**，业务层不要依赖它取字节。"""
        ...

    def read(self, location: Location) -> object: ...
    def write(self, location: Location, value: object) -> None: ...

    def create(self, location: Location, value: object) -> bool:
        """只在目标**不存在**时写入；返回是否写成（已存在返回 `False`）。

        `write` 是覆盖语义（重跑幂等），做不了「谁先到谁算数」。发布版号的分配
        必须有这种原子性：两个 API 实例同时算出 `v3`，只有一个能创建成功，
        另一个拿到 `False` 后改用 `v4`（T20）。本地实现是独占创建（`O_EXCL`），
        对象存储是 `If-None-Match: *` 条件写。
        """
        ...

    def exists(self, location: Location) -> bool: ...

    def list_keys(self, prefix: str, *, suffix: str = "") -> list[str]:
        """枚举前缀下的 key（递归），按字典序返回。

        **枚举必须走这里，不要在 `root()` 上 `glob`。** 后者把「有本地根目录」
        又塞回协议里：远端驱动直接抛错，封装列表与任务发现整块不可用（T17）。
        本地驱动用目录遍历，对象存储用 `ListObjectsV2`，返回的都是存储无关 key，
        调用方拿去 `read` 即可。
        """
        ...

    def copy(self, source: Path, destination: Location, digest: str | None = None) -> str:
        """把本地文件写入 destination，返回 sha256；目标已存在且摘要不符则拒绝覆盖。"""
        ...

    def link(self, source: Path, destination: Location, digest: str | None = None) -> str:
        """比 `copy` 更省：同一文件系统硬链接，远端驱动走服务端 copy。

        语义与 `copy` 相同的可见性保证（目标已存在且摘要不符则拒绝），但允许
        实现省掉字节搬运。调用方必须保证 `source` 之后不再被原地修改——
        内容寻址的 blob 满足这一点，临时上传文件不满足。
        """
        ...

    def materialize(self, location: Location) -> AbstractContextManager[Path]:
        """交出可读的本地路径；退出时远端驱动负责回收缓存。"""
        ...

    def download_url(self, location: Location, *, expires_in: int = 900) -> str | None:
        """私有对象的临时直连地址；本地类驱动返回 `None`。

        为什么不让调用方一律 `materialize`：视频要拖进度条，而 `FileResponse`
        只有拿到本地文件才支持 Range。远端驱动下先下完整个 300MB 才能出首帧，
        等于把「流式」变成「先下再放」。返回地址让对象存储自己处理 Range，
        字节也不经过应用进程（T19）。

        `None` 是**有意义的返回值**（本地驱动、运行工作区文件），不是"没实现"。
        拿不到地址时调用方退回 `materialize` + `FileResponse`。
        """
        ...

    def modified_at(self, location: Location) -> datetime | None:
        """对象最后修改时间；不存在返回 `None`。

        目前只有 `app prune-blobs` 用它给回收加保护期：登记流程是「先写字节、
        再写库行」，两件事之间有一小段窗口，此期间 blob 没有任何引用。
        没有时间维度就只能靠"别在上传时跑回收"这种口头约定。
        """
        ...

    def remove(self, location: Location) -> None: ...


def within(base: Path, relative: str) -> Path:
    path = (base / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(base.resolve()):
        raise BadRequest("文件路径超出归档目录")
    return path


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path) -> object:
    """读本地 JSON，容忍共享盘原子替换暴露出的瞬时缺失。

    SMB 上 `rename` 不保证 POSIX 语义，桥接层每次 update() 都会重写 run.json，
    覆盖瞬间路径可能短暂不存在（实测轮询期间读到 FileNotFoundError，接口随之 500）。
    读方必须自己跨过这个窗口，调用方不需要处理「文件刚被替换」。
    """
    for attempt in range(FILE_READ_RETRIES):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            if attempt == FILE_READ_RETRIES - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


def write_json(path: Path, value: object) -> None:
    """写本地 JSON：原子替换遇短暂共享冲突时有限重试，读方不会看到半份。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(FILE_REPLACE_RETRIES):
            try:
                temporary.replace(path)
                return
            except OSError as exc:
                transient = getattr(exc, "winerror", None) in (5, 32, 33) or exc.errno in (
                    errno.EACCES,
                    errno.EBUSY,
                    errno.EPERM,
                )
                # 不重放写入/上游请求，不清理或重建目标；明确的守卫拒绝也不能重试。
                if (
                    not transient
                    or "[safe-delete]" in str(exc)
                    or attempt == FILE_REPLACE_RETRIES - 1
                ):
                    raise
                time.sleep(0.05 * 2**attempt)
    finally:
        # 重试最终失败时临时文件会留在 records/ 里，被后续目录枚举当成正式产物扫到；
        # 成功时 replace 已经把它搬走，这里是无操作。
        temporary.unlink(missing_ok=True)
