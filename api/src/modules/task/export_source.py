"""任务模块的导出源：把 `video_tasks` 交给导出功能，但不 import 导出模块。

注册在 `__init__.py` 的 import 期完成（副作用导入）。导出模块只认
`core.ports.export_sources` 的协议，两边互不认识。

数据范围：**只导自己的任务**。有 `task:read:all` 的人能在页面上看别人的任务，
但导出不做这件事——「一键带走全公司数据」是另一种事故，和「能不能看」不是一回事。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.task.models import VideoTask


def _as_datetime(value: object) -> datetime | None:
    """params 从 JSON 列读回来时是 ISO 字符串，这里统一转回 datetime。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


class TaskExportSource:
    name: str = "tasks"
    label: str = "视频任务"
    columns: tuple[str, ...] = (
        "id",
        "title",
        "status",
        "model",
        "duration_sec",
        "progress",
        "favorited",
        "created_at",
        "finished_at",
        "error_message",
    )
    # 只要 export:read 即可；数据范围由下面那句 where 保证，与权限无关
    permission: str | None = None

    async def fetch(
        self,
        session: AsyncSession,
        *,
        actor_id: str,
        params: dict[str, Any],
        limit: int,
    ) -> list[Sequence[Any]]:
        stmt = select(VideoTask).where(VideoTask.author_id == actor_id)

        status = params.get("status")
        if isinstance(status, str) and status:
            stmt = stmt.where(VideoTask.status == status)

        model_id = params.get("model_id")
        if isinstance(model_id, str) and model_id:
            stmt = stmt.where(VideoTask.model_id == model_id)

        since = _as_datetime(params.get("since"))
        if since is not None:
            stmt = stmt.where(VideoTask.created_at >= since)

        until = _as_datetime(params.get("until"))
        if until is not None:
            stmt = stmt.where(VideoTask.created_at < until)

        # 排序键以主键收尾：同一毫秒创建的两行必须有稳定顺序，
        # 否则「截断到 limit」时每次导出的最后几行可能不同。
        stmt = stmt.order_by(VideoTask.created_at.desc(), VideoTask.id.desc())
        # LIMIT 下推：不要把全表拉回进程再切片
        stmt = stmt.limit(limit)

        rows = (await session.execute(stmt)).scalars().all()
        return [
            [
                r.id,
                r.title,
                r.status,
                r.model,
                r.duration_sec,
                r.progress,
                r.favorited,
                r.created_at,
                r.finished_at,
                r.error_message,
            ]
            for r in rows
        ]


TASK_EXPORT_SOURCE = TaskExportSource()
