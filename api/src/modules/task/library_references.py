"""`LibraryReferenceChecker` 的任务模块实现：`video_tasks.reference_library_ids`。

任务行存的是**资产库 v2 的 id**（前端先上传拿 id 再下发），所以归 v2 这套删除
保护管，不是旧 `assets` 那套。以前这一列叫 `reference_asset_ids`，架构守卫因此
一直让本模块声明一个 v1 `AssetReferenceChecker`——查的是一列里根本不存在的 v1 id，
而真正会出事的那条（v2 彻底删除放行）没有任何人拦：删掉一张被视频任务引用的图，
任务详情页的参考图直接裂，且不可逆。

口径：**只要任务行还在，就算引用**。已失败 / 已取消的任务也算——用户仍会打开
详情页看「当时用的是哪几张图」，那正是排查失败原因的唯一线索。结论与生图作业
一致（见 `modules/image/library_references.py`），因为两边的行都属于「历史证据」。

本类用的是调用方事务里的 `session`（与 v2 口径一致），不自己开连接：
删除路径要在同一个事务里判、同一个事务里删。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.task.models import VideoTask

__all__ = ["TaskLibraryReferences"]

# 一次最多查多少个 id。删除接口只传一个；批量清理（运维脚本）可能传一批，
# 给个上限避免把「几百个 OR 条件」拼进一条 SQL。
_MAX_IDS = 50


def _escape_like(value: str) -> str:
    """`%` / `_` 是 LIKE 的通配符，id 里真出现它们时会被当成模式。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class TaskLibraryReferences:
    """视频任务的参考图里出现的 v2 资产 id。"""

    async def references(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]:
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id][:_MAX_IDS]
        if not wanted:
            return {}

        found: dict[str, list[str]] = {}
        for asset_id in wanted:
            # JSON 列没法按元素查（SQLite / PG 语法不同），先按子串粗筛，
            # 再在 Python 里精确比对。粗筛只会多捞行，不会漏。
            rows = await session.execute(
                select(
                    VideoTask.id,
                    VideoTask.title,
                    VideoTask.reference_library_ids,
                ).where(
                    cast(VideoTask.reference_library_ids, String).like(
                        f"%{_escape_like(asset_id)}%", escape="\\"
                    )
                )
            )
            for task_id, title, ids in rows.all():
                if asset_id in (ids or []):
                    found.setdefault(asset_id, []).append(f"视频任务《{title}》({task_id})")
        return found
