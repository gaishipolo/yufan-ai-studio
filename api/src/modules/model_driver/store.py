"""`driver_jobs` ↔ `GenerationRecord` 的吸收层。

不负责 commit，由 `generation.runner` 统一提交。这一层存在的唯一理由是：
框架只认通用记录视图，不认识本模块的表。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from generation.spec import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    GenerationRecord,
)
from modules.model_driver.models import (
    KIND_MODEL_DRIVER,
    TERMINAL_STATUSES,
    DriverJob,
    decode_request,
)


async def _guard(session: AsyncSession, record_id: str) -> DriverJob | None:
    """终态保护：迟到的回调不许改写已结束（含已取消）的记录。

    先 flush 再 refresh，与 `modules/image/pipeline.py::guard` 同一口径：
    不刷新会读到 identity map 里的旧对象，刷新不 flush 会丢掉本会话刚写的
    「提交意图已清除」标记。
    """
    job = await session.get(DriverJob, record_id)
    if job is None:
        return None
    await session.flush()
    await session.refresh(job)
    return None if job.status in TERMINAL_STATUSES else job


class DriverJobStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _to_record(job: DriverJob) -> GenerationRecord:
        decoded = decode_request(job.params_json)
        # cancelled 也算终态：取消后 runner.handle_poll 会直接早退，
        # 不会继续对供应商轮询、也不会把状态改回 running。
        return GenerationRecord(
            id=job.id,
            kind=KIND_MODEL_DRIVER,
            model=job.model,
            prompt=job.prompt,
            status=job.status,
            owner_id=job.owner_id,
            params={"capability": job.capability, "params": decoded["params"]},
            reference_urls=tuple(decoded["referenceUrls"]),
            external_id=job.external_id,
            progress=job.progress,
            output_url=job.output_url,
            cover_url=job.cover_url or None,
            error=job.error_message,
            created_at=job.created_at,
            submit_attempts=job.submit_attempts,
            submit_mode=job.submit_mode,
            terminal_statuses=tuple(sorted(TERMINAL_STATUSES)),
            timeout_started_at=job.started_at,
        )

    async def load(self, record_id: str) -> GenerationRecord | None:
        job = await self.session.get(DriverJob, record_id)
        return None if job is None else self._to_record(job)

    async def list_unfinished(
        self, session: AsyncSession, limit: int
    ) -> Sequence[GenerationRecord]:
        """框架启动恢复用：捞未终态记录，按创建时间排序并以主键收尾。"""
        rows = (
            await session.scalars(
                select(DriverJob)
                .where(DriverJob.status.in_((STATUS_QUEUED, STATUS_RUNNING)))
                .order_by(DriverJob.created_at, DriverJob.id)
                .limit(limit)
            )
        ).all()
        return [self._to_record(row) for row in rows]

    async def mark_submitting(self, record_id: str) -> None:
        job = await _guard(self.session, record_id)
        if job is None:
            return
        job.submit_attempts += 1
        job.started_at = datetime.now(tz=UTC)

    async def clear_submit_attempt(self, record_id: str) -> None:
        job = await _guard(self.session, record_id)
        if job is None:
            return
        job.submit_attempts = 0

    async def mark_submitted(self, record_id: str, external_id: str) -> None:
        job = await _guard(self.session, record_id)
        if job is None:
            return
        job.external_id = external_id
        job.status = STATUS_RUNNING
        job.started_at = datetime.now(tz=UTC)
        job.progress = max(job.progress, 1)
        job.submit_attempts = 0
        job.submit_mode = self._snapshot_submit_mode(job)

    @staticmethod
    def _snapshot_submit_mode(job: DriverJob) -> str | None:
        """提交成功时把供应商形态写进记录，恢复时不必重新解析当前模型目录。"""
        from infra import get_model_router

        try:
            router = get_model_router()
            if router.resolve(job.model) is None:
                return None
            provider = router.provider_for(job.model)
        except Exception:  # noqa: BLE001 - 快照是增强，不能让一次成功提交失败
            return None
        return "async" if getattr(provider, "supports_poll", False) else "sync"

    async def mark_progress(self, record_id: str, progress: int) -> None:
        job = await _guard(self.session, record_id)
        if job is None:
            return
        job.progress = min(99, max(job.progress, progress))
        job.status = STATUS_RUNNING

    async def mark_succeeded(
        self,
        record_id: str,
        *,
        output_url: str | None,
        cover_url: str | None,
        progress: int = 100,
    ) -> None:
        job = await _guard(self.session, record_id)
        if job is None:
            return
        job.status = STATUS_SUCCEEDED
        job.progress = 100
        job.output_url = output_url
        if cover_url:
            job.cover_url = cover_url
        job.error_message = None
        job.finished_at = datetime.now(tz=UTC)

    async def mark_failed(self, record_id: str, error: str) -> None:
        job = await _guard(self.session, record_id)
        if job is None:
            return
        job.status = STATUS_FAILED
        job.error_message = error
        job.finished_at = datetime.now(tz=UTC)
