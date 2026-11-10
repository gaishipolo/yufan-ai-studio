"""统一生成门面：`core/ports/model_generation` 的实现。

下游模块只调这里的四个方法（submit / query / cancel / models），
不需要知道驱动文件、模型路由、任务表，也不需要自己实现 `GenerationStore`。
每次提交落一行 `driver_jobs` 后交给 `generation.runner` 编排，所以队列、
轮询、退避重试、墙钟超时、启动恢复与 `generation` 现有能力完全一致。
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.exceptions import BadRequest, Conflict, InvalidState, NotFound
from core.ports.model_generation import (
    DriverSummary,
    GenerationTicket,
    ModelSummary,
    SubmitSpec,
)
from db.session import get_session_factory
from generation.runner import get_runner
from infra import get_model_router
from infra.ai.base import Capability
from modules.model_driver.models import (
    KIND_MODEL_DRIVER,
    STATUS_CANCELLED,
    TERMINAL_STATUSES,
    DriverJob,
    encode_request,
)
from modules.model_driver.registry import driver_summaries, ensure_drivers_registered

STATUS_FAILED = "failed"
STATUS_QUEUED = "queued"


class ModelGenerationFacade:
    @staticmethod
    def _ticket(job: DriverJob) -> GenerationTicket:
        return GenerationTicket(
            task_id=job.id,
            model=job.model,
            capability=job.capability,
            status=job.status,
            progress=job.progress,
            external_id=job.external_id,
            output_url=job.output_url,
            cover_url=job.cover_url or None,
            error=job.error_message,
            owner_id=job.owner_id,
            source=job.source,
            submit_attempts=job.submit_attempts,
            created_at=job.created_at,
        )

    async def submit(self, request: SubmitSpec) -> GenerationTicket:
        if not request.model.strip() or not request.prompt.strip():
            raise BadRequest("model 与 prompt 不能为空")

        ensure_drivers_registered()
        spec = get_model_router().resolve(request.model)
        if spec is None:
            raise BadRequest(f"未知模型 {request.model!r}，请先查模型目录")

        job = DriverJob(
            name=(request.name.strip() or request.prompt.strip()[:60] or "统一生成"),
            prompt=request.prompt,
            model=request.model,
            capability=spec.capability.value,
            params_json=encode_request(dict(request.params), tuple(request.reference_urls)),
            source=request.source[:64],
            owner_id=request.owner_id,
        )
        factory = get_session_factory()
        async with factory() as session:
            session.add(job)
            await session.flush()
            ticket = self._ticket(job)
            await session.commit()

        # 落库成功后才入队：进程被杀时恢复器会从 driver_jobs 里把 queued 捞回来。
        await get_runner().dispatch(KIND_MODEL_DRIVER, ticket.task_id)
        return ticket

    async def query(self, task_id: str) -> GenerationTicket | None:
        async with get_session_factory()() as session:
            job = await session.get(DriverJob, task_id)
            return None if job is None else self._ticket(job)

    async def cancel(self, task_id: str) -> GenerationTicket | None:
        async with get_session_factory()() as session:
            job = await session.get(DriverJob, task_id)
            if job is None:
                return None
            if job.status in TERMINAL_STATUSES:
                raise Conflict(f"任务已结束（{job.status}），不能取消")

            # 已经提交到供应商的要真的调驱动的 cancel；没提交的只需改本地状态。
            if job.external_id:
                provider = get_model_router().provider_for(job.model)
                try:
                    await provider.cancel(job.external_id)
                except Exception as exc:  # noqa: BLE001 - 供应商拒绝取消要报给调用方
                    raise BadRequest(f"供应商取消失败：{exc}") from exc

            job.status = STATUS_CANCELLED
            job.finished_at = datetime.now(tz=UTC)
            ticket = self._ticket(job)
            await session.commit()
            return ticket

    async def retry(self, task_id: str, *, confirm_charged: bool = False) -> GenerationTicket:
        """人工自救出口：把 failed 复位成 queued 并重新投递。

        与 `modules/image/service.py::retry` 同一口径：带 external_id 的失败
        说明供应商可能已受理并计费，默认拒绝，必须显式 `confirmCharged=true`。
        失败也必须清 `submit_attempts`，否则 `runner` 会把它判成「提交结果未知」
        再次置死，用户点多少次都是同一个结果。
        """
        async with get_session_factory()() as session:
            job = await session.get(DriverJob, task_id)
            if job is None:
                raise NotFound("生成任务不存在")
            if job.status != STATUS_FAILED:
                raise InvalidState("仅失败的生成任务可以重试")
            if job.external_id and not confirm_charged:
                raise InvalidState(
                    f"该任务已提交到供应商（任务号 {job.external_id}），可能已产生费用："
                    "请先用任务号核对；确认要重新生成时带 confirmCharged=true"
                )

            job.external_id = None
            job.submit_attempts = 0
            job.submit_mode = None
            job.status = STATUS_QUEUED
            job.progress = 0
            job.error_message = None
            job.finished_at = None
            ticket = self._ticket(job)
            await session.commit()

        await get_runner().dispatch(KIND_MODEL_DRIVER, ticket.task_id)
        return ticket

    def models(self, capability: str = "") -> tuple[ModelSummary, ...]:
        ensure_drivers_registered()
        try:
            wanted = Capability(capability) if capability else None
        except ValueError as exc:
            raise BadRequest(f"未知能力 {capability!r}") from exc
        return tuple(
            ModelSummary(
                id=spec.id,
                label=spec.label,
                capability=spec.capability.value,
                provider=spec.provider,
                param_schema=dict(spec.param_schema),
                description=spec.description,
            )
            for spec in get_model_router().models(wanted)
        )

    def drivers(self) -> tuple[DriverSummary, ...]:
        return driver_summaries()
