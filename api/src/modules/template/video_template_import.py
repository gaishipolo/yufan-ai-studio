"""把老的 `catalog.json` 视频模板目录导入 `video_templates` 表（幂等）。

**为什么需要它**：模板目录从 JSON 文件搬到表之后，存量数据不会自己跟过来。
没有这一步，「升级」等于「模板库清空」——而且清得**不报错**，用户只会发现
「全部模板」页空了。

三条与 `import_legacy.py` 相同的约束：

1. **幂等**：主键沿用老 JSON 里的 `id`，第二次跑只更新不复制，中断可重跑；
2. **不删老文件**：`catalog.json` 留作回滚依据与 `--dry-run` 的对比基准；
3. **不猜但要认两种写法**：老 JSON 存的是 `model_dump(by_alias=True)` 的
   camelCase（`video_template_store._serialize`），但历史文件里也出现过
   snake_case。两种都认，缺字段按默认值补并把补了什么打进报告——猜错的表现是
   「导入成功但字段全是默认值」，比报错难查得多。

**固定蓝本只能留一条**：`uq_video_templates_pinned` 是这个库的硬约束，
而老目录理论上也可能有多条 `pinned`（旧实现靠 `_LOCK` 保证，多 worker 下
未必真的成立）。导入时只保留 `createdAt` 最新那条，其余清标记并记一条 note。

**`template_key` 重复也跳过**：旧实现的 `_append_record` 把它当重试稳定标识
（同键同配置复用、同键不同配置抛 Conflict），所以真实目录里不该有重复。
文件是人工可编辑的，真出现时按「先入为主」跳过、在报告里点名两条 id ——
一条坏数据不该让整份导入失败。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions import BadRequest
from modules.template.id_space import ASSET_ID_SPACE_V1
from modules.template.models import VideoTemplate

#: .../api/src/modules/template/ -> parents[3] = .../api
_API_ROOT = Path(__file__).resolve().parents[3]

#: 老目录文件的默认位置。`VIDEO_TEMPLATE_CATALOG` 配了就按配的走——它当年
#: 是「目录文件位置」，现在只剩「导入源」这一个用途。
DEFAULT_CATALOG_PATH = _API_ROOT / "var" / "video_templates" / "catalog.json"


def legacy_catalog_path() -> Path:
    configured = settings.video_template_catalog.strip()
    return Path(configured).expanduser() if configured else DEFAULT_CATALOG_PATH


@dataclass
class CatalogImportReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [
            f"新增 {self.created} 条，更新 {self.updated} 条，"
            f"未变 {self.unchanged} 条，跳过 {self.skipped} 条",
            *(f"  · {note}" for note in self.notes),
        ]


def _pick(record: dict[str, Any], *names: str, default: Any = None) -> Any:
    """两种命名都认：`templateKey` / `template_key`。"""
    for name in names:
        if name in record and record[name] is not None:
            return record[name]
    return default


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _row_from_record(record: dict[str, Any]) -> VideoTemplate | None:
    template_id = _pick(record, "id")
    if not isinstance(template_id, str) or not template_id:
        return None
    name = _pick(record, "name", default="")
    prompt = _pick(record, "prompt", default="")
    if not isinstance(name, str) or not isinstance(prompt, str):
        return None
    return VideoTemplate(
        id=template_id,
        template_key=str(_pick(record, "templateKey", "template_key", default=template_id)),
        name=name,
        description=str(_pick(record, "description", default="")),
        prompt=prompt,
        prompt_sha256=str(_pick(record, "promptSha256", "prompt_sha256", default="")),
        source_video_asset_id=_pick(record, "sourceVideoAssetId", "source_video_asset_id"),
        # 老目录里的原片 id 必然是 v1 `assets` 的：那时还没有 v2。
        # 显式写 v1 而不是靠列默认值——默认值会随 `CURRENT_ASSET_ID_SPACE` 翻转，
        # 而这份文件的内容不会。漏写的话，切换之后导入的历史模板会拿 v1 id
        # 去 v2 里找，表现是「原片不可用」。
        source_video_asset_id_space=ASSET_ID_SPACE_V1,
        # 老目录里的原片 id 必然是 v1 `assets` 的：那时还没有 v2。
        # 显式写 v1 而不是靠列默认值——默认值会随 `CURRENT_ASSET_ID_SPACE` 翻转，
        # 而这份文件的内容不会。漏写的话，切换之后导入的历史模板会拿 v1 id
        # 去 v2 里找，表现是「原片不可用」。
        source_video_path=str(_pick(record, "sourceVideoPath", "source_video_path", default="")),
        source_video_name=str(
            _pick(record, "sourceVideoName", "source_video_name", default="")
        ),
        source_duration_sec=float(
            _pick(record, "sourceDurationSec", "source_duration_sec", default=0) or 0
        ),
        aspect_ratio=str(_pick(record, "aspectRatio", "aspect_ratio", default="9:16")),
        pack_id=_pick(record, "packId", "pack_id"),
        pack_name=str(_pick(record, "packName", "pack_name", default="")),
        pack_version=int(_pick(record, "packVersion", "pack_version", default=0) or 0),
        default_kind=str(_pick(record, "defaultKind", "default_kind", default="product")),
        media_order=list(_pick(record, "mediaOrder", "media_order", default=[]) or []),
        generation=dict(_pick(record, "generation", default={}) or {}),
        rules=dict(_pick(record, "rules", default={}) or {}),
        cover_url=str(_pick(record, "coverUrl", "cover_url", default="")),
        pinned=bool(_pick(record, "pinned", default=False)),
    )


#: 导入时比较的列。时间为空（老 JSON 没写）时不该判成「有变化」，
#: 所以时间列不进比较，单独按「有值才写」处理。
_COMPARED = (
    "template_key",
    "name",
    "description",
    "prompt",
    "prompt_sha256",
    "source_video_asset_id",
    "source_video_path",
    "source_video_name",
    "source_duration_sec",
    "aspect_ratio",
    "pack_id",
    "pack_name",
    "pack_version",
    "default_kind",
    "media_order",
    "generation",
    "rules",
    "cover_url",
)


def _exists(path: Path) -> bool:
    """同 `_read_catalog`：把路径 IO 挪出 async 函数体。"""
    return path.is_file()


def _read_catalog(path: Path) -> Any:
    """同步读文件：`import_catalog` 是 async，但读一份 JSON 不值得进线程池。

    单独拆出来是为了不在 async 函数体里直接做路径 IO（`ASYNC240`）：
    这条规则在请求路径上很有价值，在一次性运维脚本上只是噪音，
    拆一层比加 `noqa` 干净。
    """
    return json.loads(path.read_text(encoding="utf-8"))


async def import_catalog(
    session: AsyncSession, path: Path, *, dry_run: bool = False
) -> CatalogImportReport:
    report = CatalogImportReport()
    if not _exists(path):
        report.notes.append(f"老目录文件不存在，无需导入：{path}")
        return report
    try:
        data = _read_catalog(path)
    except (OSError, ValueError) as exc:
        raise BadRequest(f"老目录文件无法解析：{path}") from exc
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise BadRequest(f"老目录文件结构不对（缺 items 数组）：{path}")

    records = [record for record in items if isinstance(record, dict)]
    pinned = [
        record
        for record in records
        if bool(_pick(record, "pinned", default=False))
    ]
    keep_pinned: str | None = None
    if len(pinned) > 1:
        # 只留 created_at 最新那条；没有时间的一律排最后。
        epoch = datetime.min.replace(tzinfo=UTC)
        pinned.sort(key=lambda item: _as_datetime(_pick(item, "createdAt")) or epoch)
        keep_pinned = _pick(pinned[-1], "id")
        report.notes.append(
            f"老目录里有 {len(pinned)} 条 pinned，只保留 {keep_pinned}（唯一索引只允许一条）"
        )
    elif pinned:
        keep_pinned = _pick(pinned[0], "id")

    existing = {
        row.id: row
        for row in (await session.scalars(select(VideoTemplate))).all()
    }
    # `template_key` 是「脚本重试时的稳定标识」，表上有唯一约束。老实现的
    # `_append_record` 对它做过去重：同键同配置复用、同键不同配置抛 Conflict ——
    # 也就是说真实目录里本来就不该出现两行同键。但 `catalog.json` 是人工可编辑的
    # 文件，真出现时不能整份导入失败（那样一条坏数据会影响全部模板），
    # 所以这里按「先入为主」跳过后来的，并在报告里点名两条 id。
    seen_keys: dict[str, str] = {
        row.template_key: row.id for row in existing.values()
    }
    for record in records:
        row = _row_from_record(record)
        if row is None:
            report.skipped += 1
            report.notes.append(f"跳过一条无法识别的记录：{str(record)[:60]}")
            continue
        taken = seen_keys.get(row.template_key)
        if taken is not None and taken != row.id:
            report.skipped += 1
            report.notes.append(
                f"template_key 重复，跳过 {row.id}（已由 {taken} 占用：{row.template_key}）"
            )
            continue
        seen_keys[row.template_key] = row.id
        row.pinned = row.id == keep_pinned
        created_at = _as_datetime(_pick(record, "createdAt"))
        updated_at = _as_datetime(_pick(record, "updatedAt"))
        current = existing.get(row.id)
        if current is None:
            if created_at is not None:
                row.created_at = created_at
            if updated_at is not None:
                row.updated_at = updated_at
            session.add(row)
            report.created += 1
            continue
        changed = [
            name
            for name in _COMPARED
            if getattr(current, name) != getattr(row, name)
        ]
        if current.pinned != row.pinned:
            changed.append("pinned")
        if not changed:
            report.unchanged += 1
            continue
        for name in changed:
            setattr(current, name, getattr(row, name))
        if updated_at is not None:
            current.updated_at = updated_at
        report.updated += 1

    if dry_run:
        await session.rollback()
    else:
        await session.commit()
    return report
