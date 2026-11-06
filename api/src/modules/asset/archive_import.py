"""把归档目录里的 JSON 索引导入 `assets` 表（幂等，不删 JSON）。

## 为什么需要它

归档素材以前只有磁盘上的 `catalog/assets/*.json` 当索引。列表靠 `glob` 扫目录，
素材一多就慢；而「一个素材被谁引用」这类问题也没法用 SQL 回答。现在 `assets` 表
是索引，这个命令负责把历史 JSON 补进去。

## 幂等怎么保证

- 主键沿用 JSON 里的 `id`（32 位 hex），重复执行只会命中同一行，不会产生第二份。
- 只更新**真的变了**的字段：第二次执行 `updated = 0`，可以直接放进部署脚本。
- 摘要相同的跨来源记录不冲突：去重唯一索引带 `WHERE source != 'archive'`
  （见迁移 `c1d9e4b7a260`），网页上传过同一份文件不影响归档登记。
- 无论成功失败都不删 JSON：它是替换模板的输入，也是本命令的回滚依据。

## 缺文件的行怎么办

跳过并计入 `missingFile`，不当成失败。共享盘可能只挂载了一部分目录，
「索引里有、盘上没有」是运维要看的信号，不是导入要中止的理由。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging import get_logger
from core.media import StoredMedia
from modules.asset.models import Asset
from modules.asset.schema import AssetSource

logger = get_logger(__name__)

# 导入会写这些列；用来判断「这一行到底变没变」
_MUTABLE = (
    "name",
    "type",
    "mime_type",
    "size_bytes",
    "storage_key",
    "cover_key",
    "sha256",
    "category",
    "source",
    "owner_id",
    "created_at",
    "width",
    "height",
    "duration",
    "has_audio",
)


@dataclass(slots=True)
class ImportReport:
    scanned: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    missing_file: int = 0
    conflicts: int = 0
    invalid: int = 0
    dry_run: bool = False
    problems: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        head = "预演（未写入）" if self.dry_run else "导入完成"
        rows = [
            f"{head}：扫描 {self.scanned} 个索引",
            f"  新建 {self.created}，更新 {self.updated}，无变化 {self.unchanged}",
            f"  缺文件 {self.missing_file}，冲突 {self.conflicts}，无效 {self.invalid}",
        ]
        return rows + [f"  - {item}" for item in self.problems]


def _naive_utc(value: datetime) -> datetime:
    """统一成 naive UTC 再比较。

    SQLite 的 `DateTime(timezone=True)` 读回来不带 tzinfo，直接比 aware/naive
    会永远「不相等」——那样每次导入都报 updated，幂等就失去了意义。
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _fields(media: StoredMedia) -> dict[str, Any]:
    return {
        "name": media.name[:200],
        "type": media.type,
        "mime_type": media.mime_type,
        "size_bytes": media.size_bytes,
        "storage_key": media.path,
        "cover_key": media.preview_path or "",
        # 空串会在唯一索引上互相撞车；算不出摘要就用 NULL（见 models.py 注释）
        "sha256": media.sha256 or None,
        "category": media.category,
        "source": AssetSource.ARCHIVE,
        "owner_id": media.owner_id,
        "created_at": media.created_at,
        "width": media.width,
        "height": media.height,
        "duration": media.duration,
        "has_audio": media.has_audio,
    }


def _changed(row: Asset, fields: dict[str, Any]) -> bool:
    for key in _MUTABLE:
        new = fields[key]
        old = getattr(row, key)
        if isinstance(new, datetime) and isinstance(old, datetime):
            if _naive_utc(old) == _naive_utc(new):
                continue
            return True
        if old != new:
            return True
    return False


async def import_archive_index(
    session: AsyncSession, root: Path, *, dry_run: bool = False
) -> ImportReport:
    report = ImportReport(dry_run=dry_run)
    folder = root / "catalog" / "assets"
    if not folder.is_dir():
        return report

    for path in sorted(folder.glob("*.json")):
        report.scanned += 1
        try:
            media = StoredMedia.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, ValidationError) as exc:
            report.invalid += 1
            report.problems.append(f"无效索引 {path.name}：{exc}")
            continue

        if not (root / media.path).is_file():
            report.missing_file += 1
            report.problems.append(f"缺文件 {path.name} → {media.path}")
            continue

        fields = _fields(media)
        row = await session.get(Asset, media.id)
        if row is not None and row.source != AssetSource.ARCHIVE:
            # 主键撞上非归档行：不覆盖别人的记录，交给人判断
            report.conflicts += 1
            report.problems.append(f"主键冲突 {media.id} 已属于 source={row.source}")
            continue

        action = "unchanged"
        try:
            # 每条一个 savepoint：某一行撞约束时只回滚这一行，不影响已经导入的记录
            async with session.begin_nested():
                if row is None:
                    action = "created"
                    if not dry_run:
                        session.add(Asset(id=media.id, **fields))
                elif _changed(row, fields):
                    action = "updated"
                    if not dry_run:
                        for key in _MUTABLE:
                            setattr(row, key, fields[key])
        except IntegrityError as exc:
            report.conflicts += 1
            report.problems.append(f"约束冲突 {media.id}：{exc.orig}")
            continue

        if action == "created":
            report.created += 1
        elif action == "updated":
            report.updated += 1
        else:
            report.unchanged += 1

    if not dry_run:
        await session.commit()
    logger.info(
        "归档索引导入：created=%s updated=%s unchanged=%s missing=%s conflicts=%s invalid=%s",
        report.created,
        report.updated,
        report.unchanged,
        report.missing_file,
        report.conflicts,
        report.invalid,
    )
    return report
