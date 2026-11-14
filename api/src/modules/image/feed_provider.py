"""生图记录 → 任务中心的只读来源。

注册到 `core.ports.generation_feed`，任务中心不认识 `image_jobs` 这张表。
所有筛选与统计都在 SQL 里做完，只把「这一页可能用得上」的 `offset + pageSize`
条交给聚合方——聚合方会把它和视频、替换档案合并后再切一次页。

署名、URL 签名这些出参规则与 `GET /images` 保持一致：库里存的是对象 key，
出参才按当前存储驱动现签。少了这一步，卡片会拿到 `images/xxx.png` 当 URL，
本地驱动碰巧能显示，换远端驱动就整屏裂图。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, case, func, literal, or_, select

from core.ports.generation_feed import (
    GenerationFeedPage,
    GenerationFeedQuery,
    GenerationFeedStats,
    ImageFeedItem,
)
from db.session import get_session_factory
from generation.spec import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
)
from infra.artifacts import artifact_urls
from modules.image.models import ImageJob

_HAS_OUTPUT = and_(ImageJob.output_url.is_not(None), ImageJob.output_url != "")

# 与任务中心的状态页签一一对应。`succeeded` 但成图不在 = 待处理，不是已完成：
# 否则用户会看到一个「已完成」却点不出图的任务（替换档案同款口径）。
_GROUP = case(
    (ImageJob.status.in_([STATUS_QUEUED, STATUS_RUNNING]), literal("running")),
    (ImageJob.status == STATUS_FAILED, literal("failed")),
    (and_(ImageJob.status == STATUS_SUCCEEDED, _HAS_OUTPUT), literal("completed")),
    else_=literal("attention"),
)

_GROUPS = ("running", "completed", "failed", "attention")


def _conditions(query: GenerationFeedQuery) -> list[Any]:
    """范围 / 关键词 / 只看成图：统计与列表共用同一套 where，避免两个数字。"""
    conditions: list[Any] = []
    # 数据范围只在来源方自己有跨用户开关时生效。没有 `image:read:all` 的人
    # 即便被聚合方传了 scope=all，也只能看自己的生图记录。
    if not (query.can_read_all and query.scope != "mine"):
        conditions.append(ImageJob.owner_id == query.actor_id)
    if query.only_authorized:
        conditions.append(_HAS_OUTPUT)
    keyword = query.keyword.strip()
    if keyword:
        pattern = f"%{keyword}%"
        conditions.append(
            or_(
                ImageJob.name.ilike(pattern),
                ImageJob.prompt.ilike(pattern),
                ImageJob.model.ilike(pattern),
                ImageJob.id.ilike(pattern),
                ImageJob.external_id.ilike(pattern),
            )
        )
    return conditions


def _status_condition(status: str) -> Any:
    if status in _GROUPS:
        return literal(status) == _GROUP
    if status == "queued":
        return ImageJob.status == STATUS_QUEUED
    return None


def _sort(stmt: Any, sort: str) -> Any:
    # 每条排序都以主键收尾：时间戳相同的行在翻页边界上顺序不定（与
    # `TaskRepository.apply_sort` / `ImageService.list_jobs` 同一条纪律）。
    if sort == "createdAt_asc":
        return stmt.order_by(ImageJob.created_at.asc(), ImageJob.id.asc())
    if sort == "title_asc":
        return stmt.order_by(ImageJob.name.asc(), ImageJob.id.asc())
    return stmt.order_by(ImageJob.created_at.desc(), ImageJob.id.desc())


async def read_generation_feed(query: GenerationFeedQuery) -> GenerationFeedPage:
    """查库 → 统计 → 候选；`limit=0` 时只回统计与总数，不装配条目。"""
    base = _conditions(query)
    status_condition = _status_condition(query.status)
    filtered = [*base, status_condition] if status_condition is not None else base

    async with get_session_factory()() as session:
        stat_stmt = select(_GROUP, func.count()).where(*base).group_by(_GROUP)
        rows = (await session.execute(stat_stmt)).all()
        stats = GenerationFeedStats()
        for group, count in rows:
            stats.all += int(count)
            if group in _GROUPS:
                setattr(stats, group, getattr(stats, group) + int(count))

        total = int((await session.execute(select(func.count()).where(*filtered))).scalar_one())
        if query.limit <= 0 or total == 0:
            return GenerationFeedPage(items=[], stats=stats, total=total)

        jobs = list(
            (
                await session.scalars(
                    _sort(select(ImageJob).where(*filtered), query.sort).limit(query.limit)
                )
            ).all()
        )
        # 一次批量签名，别为每张图重建一次存储驱动实例。
        urls = await artifact_urls(
            [value for job in jobs for value in (job.cover_url, job.output_url)]
        )
        items = [
            ImageFeedItem(
                id=job.id,
                title=job.name,
                status=job.status,
                group=_group_of(job),
                created_at=job.created_at,
                progress=job.progress,
                model=job.model,
                model_id=job.model_id,
                size=job.size,
                count=job.count,
                prompt=job.prompt,
                reference_library_ids=list(job.reference_library_ids or []),
                text_library_ids=list(job.text_library_ids or []),
                cover_url=urls[index * 2],
                output_url=urls[index * 2 + 1] or None,
                error_message=job.error_message,
                external_id=job.external_id,
                detail_url=f"/image?job={job.id}",
            )
            for index, job in enumerate(jobs)
        ]
        return GenerationFeedPage(items=items, stats=stats, total=total)


def _group_of(job: ImageJob) -> str:
    if job.status in (STATUS_QUEUED, STATUS_RUNNING):
        return "running"
    if job.status == STATUS_FAILED:
        return "failed"
    if job.status == STATUS_SUCCEEDED and job.output_url:
        return "completed"
    return "attention"
