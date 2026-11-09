"""把旧素材库（`assets` 表）导入资产库 v2。

## 为什么用原生 SQL 读旧表

模块之间不许互相 import（`tests/test_architecture.py` 零容忍）。这里读的是
**一次性迁移的源表**，不是运行期依赖：只取 12 个稳定列，不 import 旧 ORM、
不碰旧业务规则。表名与列名是这次迁移的输入契约，写在这里比造一个「旧资产
枚举注入口」诚实——后者只为一个命令存在，还会把旧模块的生命周期绑到 v2 上。

## 幂等

每条旧行用 `source_key = "legacy:<旧 id>"` 落库。重复执行时命中即跳过，
第二遍的 `imported=0`。字节按内容寻址写进 `library/blobs/`，与旧归档盘
（`blobs/`）物理隔离——回滚删 v2 数据不会误删旧库。

## dry-run

只统计「能导多少、已有多少、格式不支持多少」，**不读字节**。真实导入才会
materialize 旧行、重算摘要、生成缩略图；dry-run 读一遍 300MB 原片没有意义。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from infra.archive.base import ArchiveStorage
from infra.media_probe import EXTENSIONS, create_preview
from infra.storage.base import ObjectStorage
from modules.library.models import LibraryAsset
from modules.library.service import blob_key, preview_key

__all__ = ["ImportReport", "import_legacy_assets"]

logger = logging.getLogger(__name__)

#: 旧表里参与迁移的列。刻意不 `SELECT *`：新增列不该让迁移脚本的行为变化。
_LEGACY_QUERY = text(
    """
    SELECT id, name, type, mime_type, size_bytes, storage_key, sha256,
           owner_id, width, height, duration, has_audio, created_at
    FROM assets
    WHERE source = 'archive' AND deleted_at IS NULL
    ORDER BY created_at
    """
)

_SUPPORTED_KINDS = {"image", "video", "audio"}


@dataclass
class ImportReport:
    total: int = 0
    imported: int = 0
    skipped_existing: int = 0
    skipped_unsupported: int = 0
    missing_bytes: int = 0
    bytes_copied: int = 0
    dry_run: bool = False

    def lines(self) -> list[str]:
        head = f"旧素材总计 {self.total} 条" + ("（dry-run，未写库）" if self.dry_run else "")
        detail = (
            f"导入 {self.imported} 条 / 已存在 {self.skipped_existing} 条 / "
            f"格式不支持 {self.skipped_unsupported} 条 / 字节缺失 {self.missing_bytes} 条"
        )
        if self.dry_run:
            return [head, detail]
        return [head, detail, f"复制字节 {self.bytes_copied} 字节"]


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def import_legacy_assets(
    session: AsyncSession,
    archive: ArchiveStorage,
    storage: ObjectStorage,
    *,
    dry_run: bool = False,
    limit: int = 0,
    owner: str = "",
) -> ImportReport:
    report = ImportReport(dry_run=dry_run)
    rows = (await session.execute(_LEGACY_QUERY)).all()
    if owner:
        rows = [row for row in rows if row.owner_id == owner]
    if limit > 0:
        rows = rows[:limit]
    report.total = len(rows)

    for row in rows:
        source_key = f"legacy:{row.id}"
        if await session.scalar(
            select(LibraryAsset.id).where(LibraryAsset.source_key == source_key)
        ):
            report.skipped_existing += 1
            continue

        extension = Path(row.storage_key or "").suffix.lower()
        if row.type not in _SUPPORTED_KINDS or extension not in EXTENSIONS:
            # 早期归档行有「没有扩展名」的记录（迁移前遗留）。它们需要人工确认
            # 格式，脚本不猜——猜错会写出一条永远打不开的资产。
            report.skipped_unsupported += 1
            continue
        if dry_run:
            report.imported += 1
            continue

        mime = row.mime_type or EXTENSIONS[extension]
        try:
            with archive.materialize(row.storage_key) as source:
                if not source.is_file():
                    report.missing_bytes += 1
                    continue
                digest = row.sha256 or await asyncio.to_thread(_digest, source)
                key = blob_key(digest, extension)
                await storage.put_file(key, source, mime)
                cover_key = await _cover(source, digest, row.type, storage)
                size_bytes = int(row.size_bytes or source.stat().st_size)
        except Exception as exc:  # noqa: BLE001 - 单条失败不该中断整批
            report.missing_bytes += 1
            logger.warning("旧素材 %s 导入失败：%s", row.id, exc)
            continue

        session.add(
            LibraryAsset(
                name=(row.name or f"legacy{extension}")[:200],
                kind=row.type,
                origin="imported",
                mime_type=mime,
                size_bytes=size_bytes,
                sha256=digest,
                storage_key=key,
                cover_key=cover_key,
                width=int(row.width or 0),
                height=int(row.height or 0),
                duration=float(row.duration or 0.0),
                has_audio=bool(row.has_audio),
                owner_id=row.owner_id,
                visibility="personal",
                state="active",
                state_version=1,
                source_key=source_key,
                created_at=row.created_at,
            )
        )
        await session.commit()
        report.imported += 1
        report.bytes_copied += size_bytes

    return report


async def _cover(
    source: Path, digest: str, kind: str, storage: ObjectStorage
) -> str:
    """重新生成缩略图，不复用旧 `cover_key`。

    旧封面在归档存储里，v2 的取件通路是对象存储；直接把 key 抄过来会得到
    「库里有记录、取图 404」。重新生成一次是几毫秒的事，省下的是切换当天
    一整列裂开的缩略图。
    """
    if kind == "audio":
        return ""
    with tempfile.TemporaryDirectory(prefix="library-import-") as folder:
        target = Path(folder) / "cover.jpg"
        ok = await asyncio.to_thread(create_preview, source, target, kind)
        if not ok or not target.is_file():
            return ""
        key = preview_key(digest)
        await storage.put_file(key, target, "image/jpeg")
        return key
