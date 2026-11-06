"""归档盘本地/共享盘驱动。

`ARCHIVE_DRIVER=local`（默认）时由 `infra/archive/__init__.py` 按名字加载本模块。
新增实现（例如带本地缓存的 S3 网关）= 新增 `infra/archive/<name>.py` + 改 `.env`，
`infra/` 与业务模块的代码不需要动 —— 这是 A2 要修的东西。
"""

from __future__ import annotations

import json
import shutil
import uuid
from functools import lru_cache
from pathlib import Path

from core.exceptions import Conflict, UpstreamError
from infra.archive.base import checksum, within
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


Driver = LocalArchiveStorage
