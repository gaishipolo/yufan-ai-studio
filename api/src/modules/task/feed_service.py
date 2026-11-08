"""只读聚合：两边分别鉴权，合并排序后再分页，不复制或提交任务。"""

from __future__ import annotations

from datetime import UTC

from core.context import CurrentUser
from core.exceptions import UpstreamError
from core.feed import ReplacementFeedRecord, replacement_feed
from core.logging import get_logger
from core.response import PageParams
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
ACTIVE = {
    "preparing",
    "queued",
    "uploading",
    "uploaded",
    "submitting",
    "submitted",
    "running",
    "generated",
    "waiting_executor",
}
FAILED = {"prepare_failed", "failed", "timeout"}


def replacement_item(run: ReplacementFeedRecord) -> TaskFeedItem:
    group: FeedStatus = "attention"
    if run.status in ACTIVE:
        group = "running"
    elif run.status in FAILED:
        group = "failed"
    elif run.status == "success" and run.output_url:
        group = "completed"
    return TaskFeedItem(
        key=f"replacement:{run.id}",
        id=run.id,
        source="replacement",
        title=run.title,
        status=run.status,
        group=group,
        created_at=run.created_at,
        progress=run.progress or 0,
        source_name=run.source_name,
        output_url=run.output_url,
        error_message=run.last_error,
        detail_url=f"/templates?kind={run.kind}&run={run.id}",
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
    replacements: list[TaskFeedItem] = []
    upstream_ids: dict[str, str] = {}
    warnings: list[str] = []
    if source != "video" and actor.has("template:read"):
        try:
            runs = await replacement_feed(actor.id)
            replacements = [replacement_item(run) for run in runs if not run.deleted_at]
            upstream_ids = {run.id: run.task_id or "" for run in runs}
        except (OSError, UpstreamError):
            logger.exception("任务中心读取替换档案失败 owner=%s", actor.id)
            warnings.append("替换任务暂时无法读取，请检查归档盘后刷新；当前统计不含替换任务。")
    keyword = query.keyword.strip().casefold()
    replacements = [
        item
        for item in replacements
        if (
            not keyword
            or keyword in (f"{item.title} {item.id} {upstream_ids.get(item.id, '')}").casefold()
        )
        and (not query.only_authorized or item.output_url)
    ]
    stats = TaskFeedStats()
    for item in replacements:
        stats.all += 1
        setattr(stats, item.group, getattr(stats, item.group) + 1)
    base = service.repo.base_query(query, actor) if source != "replacement" else None
    if base is not None:
        video_stats = await service.repo.count_by_status(base)
        stats.all += video_stats.all
        stats.running += video_stats.running + video_stats.queued
        stats.completed += video_stats.completed
        stats.failed += video_stats.failed
    candidates = [
        item
        for item in replacements
        if status == "all"
        or item.group == status
        or (status == "queued" and item.status == "queued")
    ]
    replacement_total = len(candidates)
    if stats_only:
        # `total` 与列表口径保持一致：`stats` 是筛选前统计，不能直接拿来当总数。
        total = stats.all
        if status != "all":
            total = replacement_total + (
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
    video_total = 0
    if base is not None:
        rows, video_total = await service.repo.feed_candidates(
            base, status, query.sort, page.offset + page.limit
        )
        for row in rows:
            video = service.to_out(row)
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
                    video=video,
                )
            )
    total = video_total + replacement_total
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
