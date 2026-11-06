"""归档盘本地/共享盘驱动。

`ARCHIVE_DRIVER=local`（默认）时由 `infra/archive/__init__.py` 按名字加载本模块。
新增实现（例如带本地缓存的 S3 网关）= 新增 `infra/archive/<name>.py` + 改 `.env`，
`infra/` 与业务模块的代码不需要动 —— 这是 A2 要修的东西。
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from core.exceptions import BadRequest, Conflict, UpstreamError
from infra.archive.base import Location, checksum, read_json, within, write_json
from infra.driver_config import DriverSettings, driver_config


class ArchiveSettings(DriverSettings):
    """归档盘参数。环境变量前缀 `MEDIA_ARCHIVE_`，因此键名仍是 `MEDIA_ARCHIVE_ROOT`。

    默认值**故意留空**：以前这里写的是 `Z:/yufan_ai_studio`，换一台没映射该盘符
    的机器就报「归档盘不可用」。路径必须由部署方提供（共享盘 UNC 或本地目录），
    留空时在使用处给出带配置项名的报错，而不是猜一个盘符。
    """

    model_config = driver_config("MEDIA_ARCHIVE")

    root: str = ""


@lru_cache
def get_archive_settings() -> ArchiveSettings:
    """进程内单例。测试可用 `get_archive_settings.cache_clear()` 重置。"""
    return ArchiveSettings()


class LocalArchiveStorage:
    """同步文件驱动；异步调用方必须在线程池内完成整次操作。"""

    def root(self) -> Path:
        raw = get_archive_settings().root.strip()
        if not raw:
            raise UpstreamError(
                "素材归档盘未配置：请设置 MEDIA_ARCHIVE_ROOT（共享盘 UNC 或本地目录），"
                "不会回退本地保存"
            )
        path = Path(raw)
        if not path.is_absolute() or not path.is_dir():
            raise UpstreamError(
                f"素材归档盘不可用（MEDIA_ARCHIVE_ROOT={raw}）：路径不存在或不可访问，"
                "不会回退本地保存"
            )
        return path.resolve()

    def resolve(self, key: str) -> Path:
        return within(self.root(), key)

    def _path(self, location: Location) -> Path:
        """位置归一：`Path` 只为兼容旧调用点，新代码一律传 key。

        绝对路径必须是归档根内的——上游可能把它当"直接用这个文件"的许可，
        越过根就等于绕过了 `within` 的越界防护。
        """
        if isinstance(location, Path):
            if not location.is_absolute():
                return within(self.root(), location.as_posix())
            try:
                location.resolve().relative_to(self.root())
            except ValueError as exc:
                raise BadRequest("文件路径超出归档目录") from exc
            return location
        return within(self.root(), location)

    def exists(self, location: Location) -> bool:
        return self._path(location).is_file()

    def read(self, location: Location) -> object:
        return read_json(self._path(location))

    def write(self, location: Location, value: object) -> None:
        write_json(self._path(location), value)

    def list_keys(self, prefix: str, *, suffix: str = "") -> list[str]:
        """目录遍历实现：前缀不存在返回空表，而不是报错。

        「还没放过任何封装」与「路径写错了」在调用方看来是同一种情况——
        列表为空，不是 500。真出错（盘掉线）由 `root()` 自己抛。
        """
        base = within(self.root(), prefix.strip("/"))
        if not base.is_dir():
            return []
        root = self.root()
        keys = [p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file()]
        return sorted(key for key in keys if key.endswith(suffix))

    def copy(self, source: Path, destination: Location, digest: str | None = None) -> str:
        expected = digest or checksum(source)
        target = self._path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if checksum(target) != expected:
                raise Conflict("已有归档文件与本次输入不一致，禁止覆盖")
            return expected
        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        shutil.copyfile(source, temporary)
        if checksum(temporary) != expected:
            raise UpstreamError("归档复制校验失败，请检查共享盘连接")
        temporary.replace(target)
        return expected

    def link(self, source: Path, destination: Location, digest: str | None = None) -> str:
        """硬链接优先。

        `source` 必须是之后不会被改写的文件（内容寻址的 blob），否则硬链接会让
        目标跟着源一起变。硬链接失败（跨盘、SMB 不支持、已有文件）时退回 `copy`：
        同一个 run 目录里存两份 300MB 视频，正是共享盘从 200MB 涨到 500MB 的原因。
        """
        expected = digest or checksum(source)
        target = self._path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if checksum(target) != expected:
                raise Conflict("已有归档文件与本次输入不一致，禁止覆盖")
            return expected
        try:
            os.link(source, target)
            return expected
        except OSError:
            return self.copy(source, target, expected)

    @contextmanager
    def materialize(self, location: Location) -> Iterator[Path]:
        """本地驱动没有搬运成本：路径本身就是可读的，交出去即可。"""
        yield self._path(location)

    def remove(self, location: Location) -> None:
        self._path(location).unlink(missing_ok=True)


Driver = LocalArchiveStorage


def local_storage() -> LocalArchiveStorage:
    """显式取一份**本地**驱动，不受 `ARCHIVE_DRIVER` 影响。

    只给迁移/导入这类「要同时够得着源和目标」的工具用：搬家时目标驱动可能是
    S3，而源仍然在本地盘上。业务代码一律走 `get_archive_storage()`。
    """
    return LocalArchiveStorage()
