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
from dataclasses import replace

from sqlalchemy import case, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError, Conflict
from generation.pipeline import register_pipeline
from generation.spec import GenerationRecord
from infra.ai.base import Capability, GenerationRequest
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
        self._snapshots: dict[str, GenerationRecord] = {}

    async def load(self, record_id: str) -> GenerationRecord | None:
        run = await self.repo.get(record_id)
        if run is None:
            return None
        record = GenerationRecord(
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
        self._snapshots[record_id] = record
        return record

    async def _update(
        self,
        record_id: str,
        change: Callable[[ReplacementRun], None],
        *,
        allow_terminal_framework: bool = False,
        _retry: int = 0,
    ) -> bool:
        run = await self.repo.get_or_404(record_id)
        # Mutate a detached candidate: dirtying the session row would autoflush an
        # unconditional UPDATE before the compare-and-set below.
        original = {column.key: getattr(run, column.key) for column in run.__table__.columns}
        candidate = ReplacementRun(**original)
        change(candidate)
        values = {
            key: getattr(candidate, key)
            for key, value in original.items()
            if getattr(candidate, key) != value
        }
        statement = update(ReplacementRun).where(
            ReplacementRun.id == record_id,
            ReplacementRun.state_version == run.state_version,
            ReplacementRun.status.not_in(("success", "failed")),
        )
        if not allow_terminal_framework:
            # 常规状态推进不许碰已经收尾的执行。`mark_submitted` 是唯一例外：
            # 它补的是「上游已受理」的回执，而接管方可能已经把这一行判成
            # `submission_unknown + framework_status=failed`。用同一道终态闸门挡它，
            # 结果是任务号写不回去、没人轮询、用户被通知「可能已计费」——比终态
            # 被改更糟。业务状态仍是 `status.not_in(...)`，不会把 success 翻成 running。
            statement = statement.where(
                or_(
                    ReplacementRun.framework_status.is_(None),
                    ReplacementRun.framework_status.not_in(("success", "failed")),
                )
            )
        snapshot = self._snapshots.get(record_id)
        if snapshot is not None:
            # Manual resume starts a new execution. Results from its predecessor
            # must not finish this execution, even when the upstream ID is reused.
            statement = statement.where(
                ReplacementRun.started_at == snapshot.timeout_started_at,
                ReplacementRun.task_id
                == (snapshot.external_id.partition(":")[2] if snapshot.external_id else None),
            )
        if candidate.submit_attempts > run.submit_attempts:
            statement = statement.where(
                ReplacementRun.submit_attempts == 0, ReplacementRun.task_id.is_(None)
            )
        if "progress_percent" in values:
            values["progress_percent"] = case(
                (
                    ReplacementRun.progress_percent > candidate.progress_percent,
                    ReplacementRun.progress_percent,
                ),
                else_=candidate.progress_percent,
            )
        # Even a no-op has to validate ownership. False means a lost comparison,
        # never merely clamped/unchanged progress.
        values["state_version"] = ReplacementRun.state_version + 1
        result = await self.session.execute(
            statement.values(**values)
            .returning(ReplacementRun.state_version)
            .execution_options(synchronize_session=False)
        )
        version = result.scalar_one_or_none()
        await self.session.commit()
        if version is None:
            await self.session.refresh(run)
            if run.status in ("success", "failed"):
                return False
            if run.framework_status in ("success", "failed") and not allow_terminal_framework:
                return False
            if snapshot is not None:
                expected_task = (
                    snapshot.external_id.partition(":")[2] if snapshot.external_id else None
                )
                # All application timestamps are UTC; SQLite drops the timezone.
                current_start = run.started_at.replace(tzinfo=None) if run.started_at else None
                expected_start = (
                    snapshot.timeout_started_at.replace(tzinfo=None)
                    if snapshot.timeout_started_at
                    else None
                )
                if run.task_id != expected_task or current_start != expected_start:
                    return False
            if candidate.submit_attempts > original["submit_attempts"] and run.submit_attempts:
                return False
            # Billing/output metadata may have changed without ending this run.
            # Rebase the mutation, but never adopt another execution's snapshot.
            if _retry < 2:
                return await self._update(
                    record_id,
                    change,
                    allow_terminal_framework=allow_terminal_framework,
                    _retry=_retry + 1,
                )
            raise Conflict("任务状态持续变化，请稍后继续查询")
        if snapshot is not None:
            # Advance only to the state this transaction wrote. A refresh may see
            # a subsequent manual resume; that must not grant its execution to us.
            self._snapshots[record_id] = replace(
                snapshot,
                external_id=f"{record_id}:{candidate.task_id}" if candidate.task_id else None,
                timeout_started_at=candidate.started_at,
            )
        await self.session.refresh(run)
        # 同步一份给执行器：它读 run.json 的 status 决定「还能不能提交」。
        # 走 worker 的那一份投影，别在这里重抄一遍字段——两份投影迟早会漂移，
        # 而漂移的症状是「执行器认不出已经提交过的任务」这种最贵的那种。
        from modules.template import worker

        # 工作区不可达不该让状态更新失败：数据库已经提交，投影下次还能补。
        # 除了 OSError（盘满、权限），`run_path()` 在指针缺失时抛 NotFound、
        # 归档根没挂时抛 UpstreamError——它们同样只说明「这份投影暂时写不进去」。
        # 只吞 OSError 的后果是：一次已经落库的成功/失败被投影问题顶成异常，
        # 调用方（轮询）把它当成「这一跳失败」，多走一次退避。
        with contextlib.suppress(OSError, AppError):
            await asyncio.to_thread(worker.sync_projection, run)
        return True

    async def mark_submitting(self, record_id: str) -> bool:
        return await self._update(
            record_id, lambda run: setattr(run, "submit_attempts", run.submit_attempts + 1)
        )

    async def clear_submit_attempt(self, record_id: str) -> bool:
        return await self._update(record_id, lambda run: setattr(run, "submit_attempts", 0))

    async def mark_submitted(self, record_id: str, external_id: str) -> bool:
        def change(run: ReplacementRun) -> None:
            run.task_id = external_id.partition(":")[2]
            # 正常路径上 `schedule_state` 已经把起点落了；恢复路径可能直接从
            # `prepared` 提交过来，这里兜底补一次，别让一条真跑过的任务显示「未记录」。
            set_status(run, "running")
            run.framework_status = "running"
            run.submit_attempts = 0
            run.last_error = None
            run.submit_mode = _snapshot_submit_mode(CHANNEL_MODELS[run_channel(run.configuration)])

        # 补回执要能穿透「已被接管方判成 submission_unknown/failed」的终态标记，
        # 否则真正的任务号永远写不回去（见 _update 里 allow_terminal_framework 的注释）。
        return await self._update(record_id, change, allow_terminal_framework=True)

    async def mark_progress(self, record_id: str, progress: int) -> bool:
        def change(run: ReplacementRun) -> None:
            run.progress_percent = max(run.progress_percent, min(99, progress))

        return await self._update(record_id, change)

    async def mark_succeeded(
        self, record_id: str, *, output_url: str | None, cover_url: str | None, progress: int = 100
    ) -> bool:
        run = await self.repo.get_or_404(record_id)
        if run_channel(run.configuration) != "libtv":
            from modules.template.worker import publish_output

            await publish_output(record_id)

        def change(run: ReplacementRun) -> None:
            set_status(run, "success")
            run.framework_status = "success"
            run.progress_percent = 100
            run.last_error = None

        return await self._update(record_id, change)

    async def mark_failed(self, record_id: str, error: str) -> bool:
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

        return await self._update(record_id, change)


@register_pipeline
class ReplacementPipeline:
    kind = KIND_REPLACEMENT
    capability = Capability.VIDEO

    # 替换跑的是「原任务那次用的模型」，配置里冻着渠道与提示词。渠道停用之后
    # 绝不能靠路由的兜底换一家重跑：产物风格、单价、合规口径全变，而且看不出来。
    # 这是**声明**，不是自定义解析——怎么找供应商只有 `ModelRouter` 一处。
    strict_model = True

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
