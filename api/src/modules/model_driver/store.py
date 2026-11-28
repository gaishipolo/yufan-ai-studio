"""`driver_jobs` ↔ `GenerationRecord` 的吸收层。

不负责 commit，由 `generation.runner` 统一提交。这一层存在的唯一理由是：
框架只认通用记录视图，不认识本模块的表。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.guarded_update import update_unless_terminal
from generation.spec import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    GenerationRecord,
)
from infra.ai.base import PollCapable
from modules.model_driver.models import (
    KIND_MODEL_DRIVER,
    TERMINAL_STATUSES,
    DriverJob,
    decode_request,
)


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
        self,
        session: AsyncSession,
        limit: int | None = None,
        *,
        stale_after_sec: int | None = None,
    ) -> Sequence[GenerationRecord]:
        """框架启动/巡检恢复用：捞未终态记录，按创建时间排序并以主键收尾。"""
        stmt = select(DriverJob).where(
            DriverJob.status.in_((STATUS_QUEUED, STATUS_RUNNING))
        )
        if stale_after_sec is not None:
            cutoff = datetime.now(tz=UTC) - timedelta(seconds=max(0, stale_after_sec))
            stmt = stmt.where(DriverJob.updated_at < cutoff)
        stmt = stmt.order_by(DriverJob.created_at, DriverJob.id)
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await session.scalars(stmt)).all()
        return [self._to_record(row) for row in rows]

    async def mark_submitting(self, record_id: str) -> bool:
        return await update_unless_terminal(
            self.session,
            DriverJob,
            record_id,
            TERMINAL_STATUSES,
            {
                "submit_attempts": DriverJob.submit_attempts + 1,
                "started_at": datetime.now(tz=UTC),
            },
        )

    async def clear_submit_attempt(self, record_id: str) -> bool:
        return await update_unless_terminal(
            self.session,
            DriverJob,
            record_id,
            TERMINAL_STATUSES,
            {"submit_attempts": 0},
        )

    async def mark_submitted(self, record_id: str, external_id: str) -> bool:
        job = await self.session.get(DriverJob, record_id)
        if job is None:
            return False
        return await update_unless_terminal(
            self.session,
            DriverJob,
            record_id,
            TERMINAL_STATUSES,
            {
                "external_id": external_id,
                "status": STATUS_RUNNING,
                "started_at": datetime.now(tz=UTC),
                "progress": case(
                    (DriverJob.progress < 1, 1),
                    else_=DriverJob.progress,
                ),
                "submit_attempts": 0,
                "submit_mode": self._snapshot_submit_mode(job),
            },
        )

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
        return "async" if isinstance(provider, PollCapable) and provider.supports_poll else "sync"

    async def mark_progress(self, record_id: str, progress: int) -> bool:
        next_progress = case(
            (DriverJob.progress < progress, progress),
            else_=DriverJob.progress,
        )
        return await update_unless_terminal(
            self.session,
            DriverJob,
            record_id,
            TERMINAL_STATUSES,
            {
                "progress": case((next_progress > 99, 99), else_=next_progress),
                "status": STATUS_RUNNING,
            },
        )

    async def extend_deadline(self, record_id: str) -> bool:
        """上游还在排队：顺延墙钟起点（与 `modules/task/pipeline.py` 同口径）。"""
        return await update_unless_terminal(
            self.session,
            DriverJob,
            record_id,
            TERMINAL_STATUSES,
            {"started_at": datetime.now(tz=UTC)},
        )

    async def list_claimed_external_ids(self) -> set[str]:
        rows = (
            await self.session.scalars(
                select(DriverJob.external_id).where(DriverJob.external_id.is_not(None))
            )
        ).all()
        return {ext for ext in rows if ext}

    async def mark_succeeded(
        self,
        record_id: str,
        *,
        output_url: str | None,
        cover_url: str | None,
        progress: int = 100,
    ) -> bool:
        values: dict[str, object] = {
            "status": STATUS_SUCCEEDED,
            "progress": 100,
            "output_url": output_url,
            "error_message": None,
            "finished_at": datetime.now(tz=UTC),
        }
        if cover_url:
            values["cover_url"] = cover_url
        return await update_unless_terminal(
            self.session, DriverJob, record_id, TERMINAL_STATUSES, values
        )

    async def mark_failed(self, record_id: str, error: str) -> bool:
        return await update_unless_terminal(
            self.session,
            DriverJob,
            record_id,
            TERMINAL_STATUSES,
            {
                "status": STATUS_FAILED,
                "error_message": error,
                "finished_at": datetime.now(tz=UTC),
            },
        )
