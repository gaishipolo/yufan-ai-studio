"""把替换运行接进框架的生成流水线。

框架只认 `GenerationStore` 的七个方法（load / mark_*），不关心记录存在哪。
上一版这些方法读写 `run.json`，现在读写 `replacement_runs` 表。

**`mark_succeeded` 不再写 output_url**：旧接口要求传一个 URL 进来，实际没人用它
（成片地址由详情接口现拼）。留着这个参数会让调用方以为「传了就会生效」，
而真正决定「有没有成片」的是 `output_key` 这一列。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import BadRequest
from generation.pipeline import register_pipeline
from generation.spec import GenerationRecord
from infra.ai.base import Capability, GenerationRequest, ModelProvider
from infra.ai.libtv import MODEL_ID
from modules.template.channels import CHANNEL_MODELS, run_channel
from modules.template.models import ReplacementRun
from modules.template.repository import RunRepository
from modules.template.run_clock import set_status

KIND_REPLACEMENT = "template_replacement"


def _snapshot_submit_mode(model: str = MODEL_ID) -> str | None:
    """提交成功时把供应商形态写进记录（与 `image.pipeline` 同一口径）。

    使用任务冻结渠道对应的模型。取不到就留 `None`——快照是给恢复器分类用的，
    解析不出来宁可让它
    按「不能续查、请人核对」处理，也不猜一个可能把记录判死的答案。整段吞异常：
    一次成功提交绝不能因为这个增强动作而失败。
    """
    from infra import get_model_router

    try:
        router = get_model_router()
        if router.resolve(model) is None:
            return None
        provider = router.provider_for(model)
    except Exception:  # noqa: BLE001 - 快照是增强，不能让一次成功提交失败
        return None
    return "async" if getattr(provider, "supports_poll", False) else "sync"


class ReplacementStore:
    """`GenerationStore` 的数据库实现。

    每次 `_update` 都是一个独立事务：框架的轮询与提交在自己的任务里跑，
    与 HTTP 请求不共享事务边界——这正好也是旧实现（每次改都重写整个 run.json）
    隐含的语义。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = RunRepository(session)

    async def load(self, record_id: str) -> GenerationRecord | None:
        run = await self.repo.get(record_id)
        if run is None:
            return None
        return GenerationRecord(
            id=run.id,
            kind=KIND_REPLACEMENT,
            model=CHANNEL_MODELS[run_channel(run.configuration)],
            prompt=run.prompt,
            status=run.framework_status or "queued",
            owner_id=run.owner_id,
            external_id=f"{run.id}:{run.task_id}" if run.task_id else None,
            progress=run.progress_percent,
            error=run.last_error,
            created_at=run.created_at,
            timeout_started_at=run.started_at,
            params={"archiveRunId": run.id},
            submit_attempts=run.submit_attempts,
            submit_mode=run.submit_mode,
            terminal_statuses=("success", "failed"),
        )

    async def _update(self, record_id: str, change: Callable[[ReplacementRun], None]) -> None:
        run = await self.repo.get_or_404(record_id)
        change(run)
        await self.repo.save(run)
        await self.session.commit()
        # 同步一份给执行器：它读 run.json 的 status 决定「还能不能提交」。
        # 走 worker 的那一份投影，别在这里重抄一遍字段——两份投影迟早会漂移，
        # 而漂移的症状是「执行器认不出已经提交过的任务」这种最贵的那种。
        from modules.template import worker

        # 工作区不可达不该让状态更新失败：数据库已经提交，投影下次还能补。
        with contextlib.suppress(OSError):
            await asyncio.to_thread(worker.sync_projection, run)

    async def mark_submitting(self, record_id: str) -> None:
        await self._update(
            record_id, lambda run: setattr(run, "submit_attempts", run.submit_attempts + 1)
        )

    async def clear_submit_attempt(self, record_id: str) -> None:
        await self._update(record_id, lambda run: setattr(run, "submit_attempts", 0))

    async def mark_submitted(self, record_id: str, external_id: str) -> None:
        def change(run: ReplacementRun) -> None:
            run.task_id = external_id.partition(":")[2]
            # 正常路径上 `schedule_state` 已经把起点落了；恢复路径可能直接从
            # `prepared` 提交过来，这里兜底补一次，别让一条真跑过的任务显示「未记录」。
            set_status(run, "running")
            run.framework_status = "running"
            run.submit_attempts = 0
            run.last_error = None
            run.submit_mode = _snapshot_submit_mode(CHANNEL_MODELS[run_channel(run.configuration)])

        await self._update(record_id, change)

    async def mark_progress(self, record_id: str, progress: int) -> None:
        def change(run: ReplacementRun) -> None:
            run.progress_percent = max(run.progress_percent, min(99, progress))

        await self._update(record_id, change)

    async def mark_succeeded(
        self, record_id: str, *, output_url: str | None, cover_url: str | None, progress: int = 100
    ) -> None:
        run = await self.repo.get_or_404(record_id)
        if run_channel(run.configuration) != "libtv":
            from modules.template.worker import publish_output

            await publish_output(record_id)

        def change(run: ReplacementRun) -> None:
            set_status(run, "success")
            run.framework_status = "success"
            run.progress_percent = 100
            run.last_error = None

        await self._update(record_id, change)

    async def mark_failed(self, record_id: str, error: str) -> None:
        def change(run: ReplacementRun) -> None:
            run.framework_status = "failed"
            run.last_error = error
            if run.submit_attempts > 0 and not run.task_id:
                set_status(run, "submission_unknown")
            elif run.status == "waiting_free":
                return
            elif "占用" in error:
                set_status(run, "waiting_executor")
            else:
                set_status(run, "failed" if run.task_id else "service_error")

        await self._update(record_id, change)


@register_pipeline
class ReplacementPipeline:
    kind = KIND_REPLACEMENT
    capability = Capability.VIDEO

    def resolve_provider(self, model: str) -> ModelProvider:
        """Frozen replacement models must never use the router's development fallback."""
        from infra import get_model_router

        router = get_model_router()
        if router.resolve(model) is None:
            raise BadRequest(f"原任务的模型渠道 {model} 未启用，请恢复该渠道后继续")
        return router.provider_for(model)

    def store(self, session: AsyncSession) -> ReplacementStore:
        return ReplacementStore(session)

    async def build_request(self, record: GenerationRecord) -> GenerationRequest:
        return GenerationRequest(
            capability=self.capability,
            model=record.model,
            prompt=record.prompt,
            params=record.params,
            request_id=record.id,
        )
