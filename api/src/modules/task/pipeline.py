"""视频任务的生成流水线：把 `video_tasks` 表适配到通用生成框架。

这是**接入新生成能力的参考实现**，同事要做「图片批量生成」「配音」时，
照这 ~100 行复制改名即可，不需要理解框架内部。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from core.library_text import inject_text_refs
from core.ports.library_ingest import IngestRequest
from core.ports.library_lookup import LibraryAssetKind
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
            # 与生图同口径：把 `{{Text N}}` 展开成受理期冻结的正文，
            # 行上的 `prompt` 仍保留占位符供历史页原样展示。
            prompt=inject_text_refs(task.prompt, task.text_refs),
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

    async def mark_submitting(self, record_id: str) -> bool:
        """调用供应商前先落一次「提交意图」，崩在中途时重启能识别出来。"""
        task = await self._get(record_id)
        if task is None:
            return False
        task.submit_attempts += 1
        return True

    async def clear_submit_attempt(self, record_id: str) -> bool:
        task = await self._get(record_id)
        if task is None:
            return False
        task.submit_attempts = 0
        return True

    async def mark_submitted(self, record_id: str, external_id: str) -> bool:
        task = await self._get(record_id)
        if task is None:
            return False
        task.external_id = external_id
        task.status = STATUS_RUNNING if task.status != STATUS_QUEUED else TaskStatus.RUNNING.value
        task.started_at = datetime.now(tz=UTC)
        task.progress = max(task.progress, 1)
        # 提交意图已完成使命：不复位的话，「attempts>0」会一直残留，
        # 此后任何一次失败后的重试都会被误判为「提交结果未知」（R3-01）
        task.submit_attempts = 0
        return True

    async def mark_progress(self, record_id: str, progress: int) -> bool:
        task = await self._get(record_id)
        if task is None:
            return False
        # 进度只增不减，且不提前到达 100（终态才置 100）
        task.progress = min(99, max(task.progress, progress))
        task.status = TaskStatus.RUNNING.value
        return True

    async def mark_succeeded(
        self,
        record_id: str,
        *,
        output_url: str | None,
        cover_url: str | None,
        progress: int = 100,
    ) -> bool:
        task = await self._get(record_id)
        if task is None:
            return False
        task.status = TaskStatus.COMPLETED.value
        task.progress = 100
        task.downloadable = True
        task.output_url = output_url
        if cover_url:
            task.cover_url = cover_url
        task.error_message = None
        task.finished_at = datetime.now(tz=UTC)
        return True

    async def mark_failed(self, record_id: str, error: str) -> bool:
        task = await self._get(record_id)
        if task is None:
            return False
        task.status = TaskStatus.FAILED.value
        task.error_message = error
        task.finished_at = datetime.now(tz=UTC)
        return True


@register_pipeline
class VideoGenerationPipeline:
    kind = KIND_VIDEO
    capability = Capability.VIDEO

    # 与生图同口径：历史记录按当次那条记录的模型找回原供应商。视频单条
    # 成本更高，兜底换渠道的后果比生图更贵（时长、分辨率、计费口径全不同）。
    strict_model = True

    # 模型从目录里消失 = 这条记录不可能再有结果 → 当场判失败。
    fail_on_model_gone = True

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

    async def archive_request(
        self, session: AsyncSession, record: GenerationRecord, stored: str
    ) -> IngestRequest:
        """把一条成功的视频任务翻译成「入草稿箱」的请求（可选钩子）。

        没有这个方法时 `generation.runner._archive()` 会在 `getattr` 那一步
        静默返回——用户视频生成完了，资产库里一条都没有（2026-11-11 实测：
        图片来源入库 6 条、视频 0 条，日志里「生成结果已入草稿箱」出现 0 次）。

        `stored` 是 `persist_output` 的产物。视频这条链**没有实现落盘钩子**：
        供应商给的是长期有效的 CDN 直链（`cos-platform-outputs.../xxx.mp4`，
        不带签名参数），框架文档也写明这类能力不必落盘。所以这里拿到的就是
        直链，交给入库方按 `sourceUrl` 现取现存——字节最终落在资产库自己的
        存储里，供应商链接以后失效也不影响库里的那一份。
        """
        task = await session.get(VideoTask, record.id)
        name = (task.title if task is not None else "") or f"生成视频-{record.id[:8]}"
        is_url = stored.startswith(("http://", "https://"))
        return IngestRequest(
            source_module=KIND_VIDEO,
            source_task_id=record.id,
            # 幂等键必须带**供应商任务号**：`POST /tasks/{id}/retry` 会清空
            # `external_id` 重投（见 `service.retry_task`），只用 `video:{id}`
            # 的话重试后新出的片子会被幂等判成「已入过库」，草稿箱里永远停在
            # 上一版。同一跳轮询重复回调时 external_id 不变，仍是同一行。
            source_key=f"{KIND_VIDEO}:{record.id}:{record.external_id or 'v0'}",
            kind=LibraryAssetKind.VIDEO,
            owner_id=record.owner_id,
            name=_with_suffix(name, stored),
            storage_key="" if is_url else stored.lstrip("/"),
            source_url=stored if is_url else "",
            generation=_snapshot(task, record),
        )


def _with_suffix(name: str, stored: str) -> str:
    """给条目名补上真实产物的后缀（`mp4`）。

    用户填的是标题（「一只狸花猫在草地上奔跑」），没有后缀；资产库里存成
    无后缀的名字，下载下来双击打不开。后缀只认 `stored` 的——那是真实产物
    的格式，标题里自带什么后缀都不算数。
    """
    suffix = Path(stored.split("?", 1)[0]).suffix
    base = Path(name).stem if Path(name).suffix else name
    return f"{base}{suffix}" if suffix else base


def _snapshot(task: VideoTask | None, record: GenerationRecord) -> dict[str, object]:
    """入库快照。键名必须是资产库 `_SNAPSHOT_FIELDS` 里认得的（snake_case），
    认不出的键会被静默丢弃——写成 `durationSec` 等于没记。
    """
    if task is None:
        return {"model": record.model, "prompt": record.prompt}
    return {
        "model": task.model,
        "model_id": task.model_id or record.model,
        "prompt": task.prompt,
        "duration_sec": float(task.duration_sec),
        "aspect_ratio": task.aspect_ratio,
        "style_preset": task.style_preset or "",
        "reference_asset_ids": list(task.reference_asset_ids or []),
        "external_id": task.external_id or "",
        "generated_at": task.finished_at,
    }


__all__ = ["KIND_VIDEO", "VideoGenerationPipeline", "VideoTaskStore"]
