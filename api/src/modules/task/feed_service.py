"""只读聚合：两路分别鉴权，合并排序后再分页，不复制或提交任务。

两路各自只认自己的权限码：视频用 `task:read`，生图用 `image:read`
（跨用户还要各自的 `:read:all`）。生图不归任务模块管，所以走 `core` 的只读
注入口，不 import 同层模块。

**替换档案这一路已下线（2026-11-14 P3）**：替换不再是平行链路，而是「某个模型 +
某组槽位」的普通生成，落的就是 `tasks` 表 —— 它属于这里的第一路，不需要单独的
来源与权限码。历史 `replacement_runs` 按用户拍板直接删除，不留只读视图。
"""

from __future__ import annotations

from datetime import UTC

from sqlalchemy.exc import SQLAlchemyError

from core.context import CurrentUser
from core.exceptions import UpstreamError
from core.logging import get_logger
from core.ports.generation_feed import (
    GenerationFeedPage,
    GenerationFeedQuery,
    ImageFeedItem,
    generation_feed,
    has_generation_feed,
)
from core.response import PageParams
from infra.artifacts import artifact_urls
from modules.task.enums import TaskSort
from modules.task.feed_schema import (
    FeedSource,
    FeedStatus,
    TaskFeedItem,
    TaskFeedOut,
    TaskFeedStats,
)
from modules.task.schema import TaskListQuery
from modules.task.service import TaskService

logger = get_logger(__name__)


def image_item(item: ImageFeedItem) -> TaskFeedItem:
    """生图来源已在自己的 reader 里筛过状态、现签过地址，这里只做投影。"""
    return TaskFeedItem(
        key=f"image:{item.id}",
        id=item.id,
        source="image",
        title=item.title,
        status=item.status,
        group=item.group,
        created_at=item.created_at,
        progress=item.progress,
        output_url=item.output_url,
        error_message=item.error_message,
        detail_url=item.detail_url,
        image=item,
    )


async def list_feed(
    service: TaskService,
    actor: CurrentUser,
    query: TaskListQuery,
    page: PageParams,
    source: FeedSource,
    status: FeedStatus,
    stats_only: bool = False,
) -> TaskFeedOut:
    """`stats_only` 只算统计、不组装条目：侧栏角标每页都挂，不能拉全量列表。"""
    want_image = (
        source in ("all", "image")
        and actor.has("image:read")
        and has_generation_feed()
    )
    want_video = source in ("all", "video")

    warnings: list[str] = []
    candidates: list[TaskFeedItem] = []

    # 生图这一路把 keyword / 状态 / 排序 / 上限一起交给来源方，自行在 SQL 里
    # 完成筛选与统计。statsOnly 时 limit=0，只取数字不装配条目。
    image_page = GenerationFeedPage()
    if want_image:
        try:
            image_page = await generation_feed(
                GenerationFeedQuery(
                    actor_id=actor.id,
                    can_read_all=actor.has("image:read:all"),
                    scope=query.scope,
                    keyword=query.keyword,
                    status=status,
                    sort=str(query.sort),
                    only_authorized=query.only_authorized,
                    limit=0 if stats_only else page.offset + page.limit,
                )
            )
        except (OSError, UpstreamError, SQLAlchemyError):
            logger.exception("任务中心读取生图记录失败 owner=%s", actor.id)
            warnings.append("生图任务暂时无法读取，请稍后刷新；当前统计不含生图任务。")

    stats = TaskFeedStats()
    for name in ("all", "running", "completed", "failed", "attention"):
        setattr(stats, name, getattr(stats, name) + getattr(image_page.stats, name))
    base = service.repo.base_query(query, actor) if want_video else None
    if base is not None:
        video_stats = await service.repo.count_by_status(base)
        stats.all += video_stats.all
        stats.running += video_stats.running + video_stats.queued
        stats.completed += video_stats.completed
        stats.failed += video_stats.failed

    image_total = image_page.total

    if stats_only:
        # `total` 与列表口径保持一致：`stats` 是筛选前统计，不能直接拿来当总数。
        total = image_total + (
            await service.repo.count_candidates(base, status)
            if base is not None
            else 0
        )
        return TaskFeedOut(
            items=[],
            total=total,
            page=page.page,
            page_size=page.page_size,
            stats=stats,
            can_read_all=actor.has("task:read:all"),
            warnings=warnings,
        )
    candidates.extend(image_item(item) for item in image_page.items)
    video_total = 0
    if base is not None:
        rows, video_total = await service.repo.feed_candidates(
            base, status, query.sort, page.offset + page.limit
        )
        # 成片 / 封面在库里可能是对象 key：出参统一按当前驱动现签，别把 key
        # 当 URL 发给前端（换存储驱动后整屏裂图，且没有任何报错）。
        signed = await artifact_urls(
            [value for row in rows for value in (row.output_url, row.cover_url)]
        )
        for index, row in enumerate(rows):
            video = service.to_out(row)
            video.cover_url = signed[index * 2 + 1] or video.cover_url
            candidates.append(
                TaskFeedItem(
                    key=f"video:{video.id}",
                    id=video.id,
                    source="video",
                    title=video.title,
                    status=video.status,
                    group="running" if video.status == "queued" else video.status,
                    created_at=video.created_at,
                    progress=video.progress,
                    output_url=signed[index * 2] or None,
                    error_message=video.error_message,
                    video=video,
                )
            )
    total = video_total + image_total
    if query.sort == TaskSort.TITLE_ASC:
        candidates.sort(key=lambda item: (item.title, item.key))
    else:
        candidates.sort(
            key=lambda item: (
                item.created_at.replace(tzinfo=UTC)
                if item.created_at.tzinfo is None
                else item.created_at.astimezone(UTC),
                item.key,
            ),
            reverse=query.sort != TaskSort.CREATED_ASC,
        )
    return TaskFeedOut(
        items=candidates[page.offset : page.offset + page.limit],
        total=total,
        page=page.page,
        page_size=page.page_size,
        stats=stats,
        can_read_all=actor.has("task:read:all"),
        warnings=warnings,
    )
