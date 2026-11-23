"""视频任务 → 用量看板的只读来源。

注册到 `core.ports.usage_metrics`，看板不认识 `video_tasks` 这张表。

与任务中心的来源（`core.ports.generation_feed`）分开注册：那两个注入口回答的
是「任务中心里显示什么」，这里回答的是「这段时间里生成了多少」。口径不同，
混在一起会让「列表要显示失败任务」和「用量只算已产出」互相打架。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from core.ports.usage_metrics import (
    UsageAggregate,
    UsageQuery,
    UsageRecord,
    UsageRecordPage,
    UsageRow,
    aggregate_rows,
)
from db.session import get_session_factory
from modules.task.models import VideoTask

#: 来源标识，会出现在看板的 `sources` 里。
SOURCE = "video"

#: 生成方式（`kind` 维度）的取值。
#:
#: 由「有没有参考图」推导：视频任务没有单独的生成方式列，但参考图是图生视频
#: 的唯一硬证据 —— 有参考图就是图生视频，没有就是文生视频。
_KIND_TEXT = "文生视频"
_KIND_IMAGE = "图生视频"


def _conditions(query: UsageQuery) -> list[Any]:
    """区间 + 数据范围。统计与列表共用同一套 where，避免两个数字。"""
    conditions: list[Any] = [
        VideoTask.created_at >= query.start,
        VideoTask.created_at <= query.end,
    ]
    # 数据范围只在来源方自己有跨用户开关时生效。没有 `usage:read:all` 的人
    # 即便被看板传了 scope=all，也只能看自己的任务。
    if not (query.can_read_all and query.scope != "mine"):
        conditions.append(VideoTask.author_id == query.actor_id)
    return conditions


def _kind_of(reference_ids: list[str] | None) -> str:
    return _KIND_IMAGE if reference_ids else _KIND_TEXT


async def aggregate_usage(query: UsageQuery) -> UsageAggregate:
    """把区间内的视频任务算成汇总 / 趋势 / 构成。"""
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                select(
                    VideoTask.created_at,
                    VideoTask.status,
                    VideoTask.model,
                    VideoTask.model_id,
                    VideoTask.duration_sec,
                    VideoTask.author_id,
                    VideoTask.author_name,
                    VideoTask.reference_library_ids,
                ).where(*_conditions(query))
            )
        ).all()

    normalized = [
        UsageRow(
            created_at=created_at,
            status=status,
            model_id=model_id or "",
            model_name=model or "",
            quantity=1,
            duration_sec=duration_sec or 0,
            owner_id=author_id,
            owner_name=author_name or "",
            kind=_kind_of(reference_library_ids),
        )
        for (
            created_at,
            status,
            model,
            model_id,
            duration_sec,
            author_id,
            author_name,
            reference_library_ids,
        ) in rows
    ]
    return aggregate_rows(SOURCE, normalized, query.granularity)


async def read_usage_records(query: UsageQuery) -> UsageRecordPage:
    """取视频任务的明细。

    返回前 `offset + limit` 条（从第 0 条起）：看板把各来源的结果合并排序后
    才切页，实现方自己按 offset 翻页会让合并后的页码对不上。
    """
    fetch = query.offset + query.limit if query.limit > 0 else 0
    if fetch <= 0:
        return UsageRecordPage(items=[], total=0)

    async with get_session_factory()() as session:
        conditions = _conditions(query)
        total = int(
            (
                await session.execute(
                    select(func.count()).select_from(VideoTask).where(*conditions)
                )
            ).scalar_one()
        )
        rows = (
            await session.execute(
                select(
                    VideoTask.id,
                    VideoTask.title,
                    VideoTask.status,
                    VideoTask.model,
                    VideoTask.model_id,
                    VideoTask.duration_sec,
                    VideoTask.created_at,
                    VideoTask.author_id,
                    VideoTask.author_name,
                )
                .where(*conditions)
                .order_by(VideoTask.created_at.desc(), VideoTask.id.desc())
                .limit(fetch)
            )
        ).all()

    items = [
        UsageRecord(
            id=record_id,
            source=SOURCE,
            title=title,
            status=status,
            model_id=model_id or "",
            model_name=model or "",
            quantity=1,
            duration_sec=duration_sec or 0,
            created_at=created_at,
            owner_id=author_id,
            owner_name=author_name or "",
            detail_url=f"/tasks?source=video&keyword={record_id}",
        )
        for (
            record_id,
            title,
            status,
            model,
            model_id,
            duration_sec,
            created_at,
            author_id,
            author_name,
        ) in rows
    ]
    return UsageRecordPage(items=items, total=total)


class VideoUsageProvider:
    """视频 → 用量看板的读取器（注册到 `core.ports.usage_metrics`）。

    契约是 Protocol 的两个方法，实现写成模块级函数是为了让 SQL 那部分好单独测；
    这里只做一层薄适配。
    """

    async def aggregate(self, query: UsageQuery) -> UsageAggregate:
        return await aggregate_usage(query)

    async def records(self, query: UsageQuery) -> UsageRecordPage:
        return await read_usage_records(query)


__all__ = ["VideoUsageProvider", "SOURCE", "aggregate_usage", "read_usage_records"]
