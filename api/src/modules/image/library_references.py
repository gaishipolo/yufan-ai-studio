"""`LibraryReferenceChecker` 的生图实现：`image_jobs.reference_json`。

生图作业在**受理期**把参考图解析成快照
`{"assetId", "storageKey", "mimeType"}`（`image/service.py::_resolve_references`）。
快照里存的是 storageKey，**不是字节**：提交 / 重试时流水线还要按这个 key 去对象存储
读一遍再交给供应商。所以 v2 资产被彻底删除之后，这条作业的重试会读不到字节 ——
这是真引用，不是历史展示问题。

`text_json` 里的文本素材**不算**：那份快照冻结了正文本身，删掉资产不影响历史
可读性，也不影响已经注入过的提示词。把它算成引用会让「用过一次的文案永远删不掉」，
是过度保护；过度保护的结果是用户去关掉整道闸门，比漏保护更糟。

口径：**只要作业行还在，就算引用**。失败的作业也保留 —— 用户要重试，
而重试正是要读这份字节的那一刻。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.image.models import ImageJob, parse_references_json

__all__ = ["ImageJobReferences"]

_MAX_IDS = 50


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ImageJobReferences:
    """生图作业的参考图快照里出现的 v2 资产 id。"""

    async def references(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]:
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id][:_MAX_IDS]
        if not wanted:
            return {}
        found: dict[str, list[str]] = {}
        # JSON 列没法跨方言按元素查：先按子串粗筛（只会多捞），再精确比对。
        for asset_id in wanted:
            rows = await session.execute(
                select(ImageJob.id, ImageJob.reference_json).where(
                    cast(ImageJob.reference_json, String).like(
                        f"%{_escape_like(asset_id)}%", escape="\\"
                    )
                )
            )
            for job_id, reference_json in rows.all():
                ids = {item["assetId"] for item in parse_references_json(reference_json)}
                if asset_id in ids:
                    found.setdefault(asset_id, []).append(f"生图作业 {job_id} 的参考图")
        return found
