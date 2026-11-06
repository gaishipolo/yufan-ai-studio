"""把本地存储根目录下的对象原样搬进当前配置的存储驱动。

## 什么时候用

`STORAGE_DRIVER` 从 `local` 改成 `s3` 之后，DB 里的 `storage_key` 一个都没变，
但读路径变了：`AssetService.resolve_file` 拿 key 去签名，桶里没有这个对象就是 404。
所以切换前要先把盘上已有的对象搬过去，否则「素材列表还在、点开全空」。

## 为什么按 key 搬，不按表搬

DB 里的 `storage_key` 就是对象 key。原样搬过去一行都不用改，也就不用写迁移脚本
回填数据；代价是本地目录结构必须和 key 同构——这本来就是这个模块的约定
（`LocalStorage._resolve` 把 key 直接拼到 root 下）。

好处是本命令在切换前后任意时刻都能跑：切换前跑是预热，切换后跑是补漏。

## 幂等与重跑

同一个 key 再写一次就是覆盖，字节相同则结果相同，所以重跑不会产生第二份对象。
代价是重跑会重传全部文件（不做「已存在就跳过」：`ObjectStorage` 协议只有
读写删，没有 stat；为了省一次带宽去加一个只有迁移用得上的协议方法不划算）。

## 不删本地

默认只复制。`--delete-source` 才删。本地目录是回滚依据：`STORAGE_DRIVER=local`
改回去就能继续读；删了就只能从桶上再拉回来。
"""

from __future__ import annotations

import asyncio
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

from core.logging import get_logger
from infra.media_probe import EXTENSIONS
from infra.storage.base import ObjectStorage, StorageError

logger = get_logger(__name__)

# 上传中转目录：里面是 `AssetService._receive` 写到一半的临时文件，
# 进程崩溃才留得下来。它们不是对象，不该进桶。
_SKIP_DIRS = ("incoming",)


@dataclass(slots=True)
class MigrationReport:
    scanned: int = 0
    copied: int = 0
    failed: int = 0
    deleted: int = 0
    dry_run: bool = False
    problems: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        head = "预演（未写入）" if self.dry_run else "迁移完成"
        rows = [
            f"{head}：扫描 {self.scanned} 个文件",
            f"  已复制 {self.copied}，失败 {self.failed}，删除源文件 {self.deleted}",
        ]
        return rows + [f"  - {item}" for item in self.problems]


def _scan(root: Path) -> list[Path]:
    """同步扫盘：整段丢线程池跑，别在事件循环里做目录遍历。"""
    if not root.is_dir():
        return []
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.relative_to(root).parts[0] not in _SKIP_DIRS
    ]


def _content_type(path: Path) -> str:
    # 与上传接口用同一张表：桶里的 Content-Type 应和走 API 上传时一致，
    # 否则浏览器会按 octet-stream 下载而不是直接显示
    known = EXTENSIONS.get(path.suffix.lower())
    if known:
        return known
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


async def migrate_local_objects(
    storage: ObjectStorage,
    root: Path,
    *,
    dry_run: bool = False,
    delete_source: bool = False,
) -> MigrationReport:
    """把 `root` 下的文件按相对路径写成对象 key。

    `storage` 是**目标**驱动（`get_storage()`），不是源；源恒为本地目录。
    """
    report = MigrationReport(dry_run=dry_run)
    paths = await asyncio.to_thread(_scan, root)
    if not paths:
        report.problems.append(f"源目录为空或不存在：{root}")
        return report

    for path in paths:
        key = path.relative_to(root).as_posix()
        report.scanned += 1
        if dry_run:
            report.copied += 1
            continue
        try:
            await storage.put_file(key, path, _content_type(path))
        except (OSError, StorageError) as exc:
            # 单个文件失败不中断：迁移是长事务，停下来重跑更累
            report.failed += 1
            report.problems.append(f"{key}：{exc}")
            continue
        report.copied += 1
        if delete_source:
            try:
                await asyncio.to_thread(path.unlink)
            except OSError as exc:
                report.problems.append(f"已复制但删除源失败 {key}：{exc}")
                continue
            report.deleted += 1

    logger.info(
        "本地对象迁移：copied=%s failed=%s deleted=%s dry_run=%s",
        report.copied,
        report.failed,
        report.deleted,
        dry_run,
    )
    return report


__all__ = ["MigrationReport", "migrate_local_objects"]
