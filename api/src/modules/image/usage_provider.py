"""生图作业 → 用量看板的只读来源。

注册到 `core.ports.usage_metrics`，看板不认识 `image_jobs` 这张表。

与任务中心的来源（`core.ports.generation_feed`）分开注册：那两个注入口回答的
是「任务中心里显示什么」，这里回答的是「这段时间里生成了多少」。口径不同，
混在一起会让「列表要显示失败任务」和「用量只算已产出」互相打架。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select

from core.ports.org_directory import user_names
from core.ports.usage_metrics import (
    UsageAggregate,
    UsageQuery,
    UsageRecord,
    UsageRecordPage,
    UsageRow,
    aggregate_rows,
)
from db.session import get_session_factory
from modules.image.models import ImageJob, parse_references_json

#: 来源标识，会出现在看板的 `sources` 里。
SOURCE = "image"

#: 生成方式（`kind` 维度）的取值。
#:
#: 由「有没有参考图」推导：生图作业没有单独的生成方式列，但参考图是图生图
#: 的唯一硬证据 —— 有参考图就是图生图，没有就是文生图。
_KIND_TEXT = "文生图"
_KIND_IMAGE = "图生图"


def _conditions(query: UsageQuery) -> list[Any]:
    """区间 + 数据范围。统计与列表共用同一套 where，避免两个数字。"""
    conditions: list[Any] = [
        ImageJob.created_at >= query.start,
        ImageJob.created_at <= query.end,
    ]
    # 数据范围只在来源方自己有跨用户开关时生效。没有 `usage:read:all` 的人
    # 即便被看板传了 scope=all，也只能看自己的作业。
    if not (query.can_read_all and query.scope != "mine"):
        conditions.append(ImageJob.owner_id == query.actor_id)
    return conditions


def _kind_of(reference_json: str | None) -> str:
    return _KIND_IMAGE if parse_references_json(reference_json) else _KIND_TEXT


async def aggregate_usage(query: UsageQuery) -> UsageAggregate:
    """把区间内的生图作业算成汇总 / 趋势 / 构成。"""
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                select(
                    ImageJob.created_at,
                    ImageJob.status,
                    ImageJob.model,
                    ImageJob.model_id,
                    ImageJob.count,
                    ImageJob.owner_id,
                    ImageJob.reference_json,
                    ImageJob.template_key,
                    ImageJob.template_name,
                    ImageJob.template_version,
                ).where(*_conditions(query))
            )
        ).all()
        # 生图表只存 owner_id；关系图需要可读名，走组织目录注入口补齐。
        names = await user_names(session, [row.owner_id for row in rows])

    normalized = [
        UsageRow(
            created_at=created_at,
            status=status,
            model_id=model_id or "",
            model_name=model or "",
            # 生图按「张」计：一次作业可能出多张
            quantity=count or 0,
            owner_id=owner_id,
            owner_name=names.get(owner_id, ""),
            kind=_kind_of(reference_json),
            template_key=template_key or "",
            template_name=template_name or "",
            template_version=template_version,
        )
        for (
            created_at,
            status,
            model,
            model_id,
            count,
            owner_id,
            reference_json,
            template_key,
            template_name,
            template_version,
        ) in rows
    ]
    return aggregate_rows(SOURCE, normalized, query.granularity)


async def read_usage_records(query: UsageQuery) -> UsageRecordPage:
    """取生图作业的明细。

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
                    select(func.count()).select_from(ImageJob).where(*conditions)
                )
            ).scalar_one()
        )
        rows = (
            await session.execute(
                select(
                    ImageJob.id,
                    ImageJob.name,
                    ImageJob.status,
                    ImageJob.model,
                    ImageJob.model_id,
                    ImageJob.count,
                    ImageJob.created_at,
                    ImageJob.owner_id,
                )
                .where(*conditions)
                .order_by(ImageJob.created_at.desc(), ImageJob.id.desc())
                .limit(fetch)
            )
        ).all()

    items = [
        UsageRecord(
            id=record_id,
            source=SOURCE,
            title=name,
            status=status,
            model_id=model_id or "",
            model_name=model or "",
            quantity=count or 0,
            created_at=created_at,
            owner_id=owner_id,
            owner_name="",
            detail_url=f"/image?job={record_id}",
        )
        for (
            record_id,
            name,
            status,
            model,
            model_id,
            count,
            created_at,
            owner_id,
        ) in rows
    ]
    return UsageRecordPage(items=items, total=total)


async def read_template_counts(template_keys: Sequence[str]) -> Mapping[str, int]:
    """模板累计使用次数：按 key 计尝试数（含失败/进行中）。"""
    wanted = list(dict.fromkeys(template_keys))
    if not wanted:
        return {}
    async with get_session_factory()() as session:
        rows = await session.execute(
            select(ImageJob.template_key, func.count())
            .where(ImageJob.template_key.in_(wanted))
            .group_by(ImageJob.template_key)
        )
        return {
            template_key: int(count)
            for template_key, count in rows.all()
            if template_key
        }


class ImageUsageProvider:
    """生图 → 用量看板的读取器（注册到 `core.ports.usage_metrics`）。

    契约是 Protocol 的两个方法，实现写成模块级函数是为了让 SQL 那部分好单独测；
    这里只做一层薄适配。
    """

    async def template_counts(
        self, template_keys: Sequence[str]
    ) -> Mapping[str, int]:
        return await read_template_counts(template_keys)

    async def aggregate(self, query: UsageQuery) -> UsageAggregate:
        return await aggregate_usage(query)

    async def records(self, query: UsageQuery) -> UsageRecordPage:
        return await read_usage_records(query)


__all__ = [
    "ImageUsageProvider",
    "SOURCE",
    "aggregate_usage",
    "read_template_counts",
    "read_usage_records",
]
