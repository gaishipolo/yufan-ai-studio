"""`AssetReferenceChecker` 的任务模块实现。

「这个素材还有人用吗」的答案在这里，因为**只有本模块把素材 id 写进了自己的表**：
`video_tasks.reference_asset_ids`（生成时的参考图）。素材库删掉一张被任务引用的图，
任务详情页的参考图就会变成空白——而且删完不可恢复。

判定口径：**只要任务行还在，就算引用**。已失败 / 已取消的任务也算——用户仍会打开
详情页看「当时用的是哪几张图」，那正是排查失败原因的唯一线索。硬删掉任务行之后
自然就不再拦，不需要额外的状态白名单。

会话是自建的：调用方（`AssetService.delete_asset`）在自己事务里，而这里要读全库
（别人的任务也可能引用这份素材）。共用一个 session 会把它钉在调用方的事务里。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.ports.assets import AssetReferenceChecker

__all__ = ["TaskAssetReferences"]

# 一次最多查多少个 id。删除接口只传一个，批量清理（迁移、运维脚本）可能传一批，
# 给个上限避免把「几百个 OR 条件」拼进一条 SQL。
_MAX_IDS = 50


class TaskAssetReferences(AssetReferenceChecker):
    """视频任务的参考素材引用。"""

    async def references(self, asset_ids: Sequence[str]) -> Mapping[str, Sequence[str]]:
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id][:_MAX_IDS]
        if not wanted:
            return {}

        # 局部 import：本类在 `modules.task.__init__` 里注册，导入期越少越好
        from sqlalchemy import String, cast, select

        from db.session import get_session_factory
        from modules.task.models import VideoTask

        found: dict[str, list[str]] = {}
        async with get_session_factory()() as session:
            for asset_id in wanted:
                # JSON 列没法按元素查（SQLite / PG 语法不同），先按子串粗筛，
                # 再在 Python 里精确比对。粗筛只会多捞行，不会漏。
                pattern = f"%{_escape_like(asset_id)}%"
                rows = (
                    await session.execute(
                        select(
                            VideoTask.id,
                            VideoTask.title,
                            VideoTask.reference_asset_ids,
                        ).where(
                            cast(VideoTask.reference_asset_ids, String).like(pattern, escape="\\")
                        )
                    )
                ).all()
                for row in rows:
                    if asset_id in (row.reference_asset_ids or []):
                        found.setdefault(asset_id, []).append(f"视频任务《{row.title}》({row.id})")
        return found


def _escape_like(value: str) -> str:
    """`%` / `_` 是 LIKE 的通配符，id 里真出现它们时会被当成模式。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
