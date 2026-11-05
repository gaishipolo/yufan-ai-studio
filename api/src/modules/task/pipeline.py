"""视频任务的生成流水线：把 `video_tasks` 表适配到通用生成框架。

这是**接入新生成能力的参考实现**，同事要做「图片批量生成」「配音」时，
照这 ~100 行复制改名即可，不需要理解框架内部。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from generation.pipeline import register_pipeline
from generation.spec import STATUS_QUEUED, STATUS_RUNNING, GenerationRecord
from infra.ai.base import Capability, GenerationRequest
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask

KIND_VIDEO = "video"

# 任务表的终态用 completed/failed（前端契约），与框架默认的 succeeded 不同，
# 因此显式声明终态集合。
_TERMINAL = (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value)


class VideoTaskStore:
    """`video_tasks` ↔ `GenerationRecord` 的映射。不负责 commit，由框架统一提交。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _get(self, record_id: str) -> VideoTask | None:
        return await self.session.get(VideoTask, record_id)

    async def load(self, record_id: str) -> GenerationRecord | None:
        task = await self._get(record_id)
        if task is None:
            return None
        params: dict[str, object] = {
            "durationSec": task.duration_sec,
            "aspectRatio": task.aspect_ratio,
        }
        if task.style_preset:
            params["stylePreset"] = task.style_preset
        return GenerationRecord(
            id=task.id,
            kind=KIND_VIDEO,
            # 路由供应商用目录 id；历史数据可能只有可读名（ModelRouter 两种都认）
            model=task.model_id or task.model,
            prompt=task.prompt,
            status=task.status,
            owner_id=task.author_id,
            params=params,
            external_id=task.external_id,
            progress=task.progress,
            output_url=task.output_url,
            cover_url=task.cover_url or None,
            error=task.error_message,
            created_at=task.created_at,
            submit_attempts=task.submit_attempts,
            terminal_statuses=_TERMINAL,
        )

    async def mark_submitting(self, record_id: str) -> None:
        """调用供应商前先落一次「提交意图」，崩在中途时重启能识别出来。"""
        task = await self._get(record_id)
        if task is None:
            return
        task.submit_attempts += 1

    async def clear_submit_attempt(self, record_id: str) -> None:
        task = await self._get(record_id)
        if task is None:
            return
        task.submit_attempts = 0

    async def mark_submitted(self, record_id: str, external_id: str) -> None:
        task = await self._get(record_id)
        if task is None:
            return
        task.external_id = external_id
        task.status = STATUS_RUNNING if task.status != STATUS_QUEUED else TaskStatus.RUNNING.value
        task.started_at = datetime.now(tz=UTC)
        task.progress = max(task.progress, 1)
        # 提交意图已完成使命：不复位的话，「attempts>0」会一直残留，
        # 此后任何一次失败后的重试都会被误判为「提交结果未知」（R3-01）
        task.submit_attempts = 0

    async def mark_progress(self, record_id: str, progress: int) -> None:
        task = await self._get(record_id)
        if task is None:
            return
        # 进度只增不减，且不提前到达 100（终态才置 100）
        task.progress = min(99, max(task.progress, progress))
        task.status = TaskStatus.RUNNING.value

    async def mark_succeeded(
        self,
        record_id: str,
        *,
        output_url: str | None,
        cover_url: str | None,
        progress: int = 100,
    ) -> None:
        task = await self._get(record_id)
        if task is None:
            return
        task.status = TaskStatus.COMPLETED.value
        task.progress = 100
        task.downloadable = True
        task.output_url = output_url
        if cover_url:
            task.cover_url = cover_url
        task.error_message = None
        task.finished_at = datetime.now(tz=UTC)

    async def mark_failed(self, record_id: str, error: str) -> None:
        task = await self._get(record_id)
        if task is None:
            return
        task.status = TaskStatus.FAILED.value
        task.error_message = error
        task.finished_at = datetime.now(tz=UTC)


@register_pipeline
class VideoGenerationPipeline:
    kind = KIND_VIDEO
    capability = Capability.VIDEO

    def store(self, session: AsyncSession) -> VideoTaskStore:
        return VideoTaskStore(session)

    async def build_request(self, record: GenerationRecord) -> GenerationRequest:
        return GenerationRequest(
            capability=self.capability,
            model=record.model,
            prompt=record.prompt,
            params=record.params,
            request_id=record.id,
        )


__all__ = ["KIND_VIDEO", "VideoGenerationPipeline", "VideoTaskStore"]
