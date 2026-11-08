"""任务仓储：把 SQL 细节挡在 service 之外。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, or_, select

from core.context import CurrentUser
from db.repository import BaseRepository
from modules.task.enums import TaskScope, TaskSort, TaskStatus, TaskStatusFilter
from modules.task.models import VideoTask
from modules.task.permissions import PERM_READ_ALL
from modules.task.schema import TaskListQuery, TaskStats


class TaskRepository(BaseRepository[VideoTask]):
    model = VideoTask

    def base_query(self, query: TaskListQuery, actor: CurrentUser) -> Select[Any]:
        """按范围 / 授权 / 关键词过滤，返回未加状态与排序的语句。

        这里先归一成枚举再做身份比较（`TaskScope(query.scope)`），不要直接依赖
        Pydantic 字段的对象身份：曾经 `CamelModel` 开了 `use_enum_values=True`，
        显式传入的枚举会被转成 str，`query.scope is TaskScope.MINE` 恒为 False，
        作者过滤整个失效（跨用户看到全站任务）。那个配置已删，但这个归一保留——
        调用方传 str 是常态（查询串、JSON），归一比依赖注解更结实。

        数据范围只有**一个开关**：`task:read:all`。没有它时 `all` / `team` 一律
        降级为「只看自己」，与详情、删除的判定完全一致——列表能看见、点进去 403
        是最难排查的一种不一致。
        """
        stmt = select(VideoTask)

        scope = TaskScope(query.scope)
        can_read_all = actor.has(PERM_READ_ALL)

        if scope is TaskScope.MINE:
            stmt = stmt.where(VideoTask.author_id == actor.id)
        elif not can_read_all:
            # ALL / TEAM 都要求「能看他人数据」；没有该权限时安全降级而不是报错，
            # 保证前端的范围下拉在任何角色下都可用
            stmt = stmt.where(VideoTask.author_id == actor.id)
        # TEAM：团队概念接入后替换为「成员 ID 集合」；当前与 ALL 同义

        if query.only_authorized:
            stmt = stmt.where(VideoTask.downloadable.is_(True))

        keyword = query.keyword.strip()
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(
                or_(
                    VideoTask.title.ilike(pattern),
                    VideoTask.model.ilike(pattern),
                    VideoTask.id.ilike(pattern),
                )
            )

        return stmt

    def apply_status(self, stmt: Select[Any], status: TaskStatusFilter) -> Select[Any]:
        if status == TaskStatusFilter.ALL:
            return stmt
        return stmt.where(VideoTask.status == str(status))

    def apply_sort(self, stmt: Select[Any], sort: TaskSort | str) -> Select[Any]:
        match TaskSort(sort):
            # 每条排序都以主键收尾：排序键不唯一时，翻页边界上的行顺序不确定，
            # 会出现「同一条出现在两页」或「哪一页都没有」（R8-06）
            case TaskSort.CREATED_ASC:
                return stmt.order_by(VideoTask.created_at.asc(), VideoTask.id.asc())
            case TaskSort.TITLE_ASC:
                return stmt.order_by(VideoTask.title.asc(), VideoTask.id.asc())
            case _:
                return stmt.order_by(VideoTask.created_at.desc(), VideoTask.id.desc())

    async def count_by_status(self, stmt: Select[Any]) -> TaskStats:
        """一条 group by 拿到各状态计数。

        直接把过滤条件（`whereclause`）搬到聚合语句上，而不是 `stmt.subquery()`
        再套一层——后者会把所有列都塞进子查询，数据量大时是全表宽行扫。
        """
        count_stmt = select(VideoTask.status, func.count())
        if stmt.whereclause is not None:
            count_stmt = count_stmt.where(stmt.whereclause)
        count_stmt = count_stmt.group_by(VideoTask.status)

        rows = (await self.session.execute(count_stmt)).all()

        counts = {str(status): int(total) for status, total in rows}
        return TaskStats(
            all=sum(counts.values()),
            queued=counts.get(TaskStatus.QUEUED.value, 0),
            running=counts.get(TaskStatus.RUNNING.value, 0),
            completed=counts.get(TaskStatus.COMPLETED.value, 0),
            failed=counts.get(TaskStatus.FAILED.value, 0),
        )

    async def get_many(self, ids: list[str]) -> list[VideoTask]:
        if not ids:
            return []
        rows = await self.session.scalars(select(VideoTask).where(VideoTask.id.in_(ids)))
        return list(rows)

    def status_filter(self, base: Select[Any], status: str) -> Select[Any] | None:
        """按状态页签过滤；`attention` 只属于替换任务，通用任务没有对应状态。"""
        if status == "running":
            return base.where(VideoTask.status.in_(["queued", "running"]))
        if status == "attention":
            return None
        if status != "all":
            return base.where(VideoTask.status == status)
        return base

    async def count_candidates(self, base: Select[Any], status: str) -> int:
        """只算条数：统计接口要给出与列表一致的 `total`，但不拉行。"""
        stmt = self.status_filter(base, status)
        return 0 if stmt is None else await self.count(stmt)

    async def feed_candidates(
        self, base: Select[Any], status: str, sort: TaskSort | str, limit: int,
    ) -> tuple[list[VideoTask], int]:
        """合并前取前 offset+limit 条，避免把通用任务全表搬进内存。"""
        stmt = self.status_filter(base, status)
        if stmt is None:
            return [], 0
        total = await self.count(stmt)
        rows = await self.session.scalars(self.apply_sort(stmt, sort).limit(limit))
        return list(rows), total
