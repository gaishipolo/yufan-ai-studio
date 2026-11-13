"""统一生成门面：`core/ports/model_generation` 的实现。

下游模块通过这里提交 / 查询 / 取消 / 挑模型，不需要知道驱动文件、模型路由、
任务表，也不需要自己实现 `GenerationStore`。每次提交落一行 `driver_jobs` 后
交给 `generation.runner` 编排，所以队列、轮询、退避重试、墙钟超时、启动恢复
与 `generation` 现有能力完全一致。

门面还兼作「问模型层」的唯一出口：替换链的渠道别名、可用性、提交形态与
提交前素材准备都委托给 `replacement_channels.py`。这样 `modules/template`
不必自己拿 `ModelRouter` / `InputPreparer`，供应商细节不会散进业务模块。
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.exceptions import BadRequest, Conflict, InvalidState, NotFound
from core.param_schema import resolve_params
from core.ports.model_generation import (
    DriverSummary,
    ExecutorOutcome,
    GenerationTicket,
    LibTVPaths,
    ModelSummary,
    ReplacementChannelInfo,
    SubmitSpec,
)
from db.session import get_session_factory
from generation.runner import get_runner
from infra import get_model_router
from infra.ai.base import Capability
from modules.model_driver import replacement_channels as channels
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

        # 参数校验不能靠驱动自觉：门面是通用入口，不认识任何具体字段，
        # 但**知道**每个模型声明了什么。漏了这道检查，`{"count": 4}` 遇到
        # 只支持 1 张的驱动会被上游静默裁成 1 张（只计费 1 张、也不报错），
        # 未声明的键则被静默丢弃——两种都让用户以为改的值生效了。
        params = resolve_params(spec.id, spec.param_schema or {}, request.params)

        # 参考图张数同理：`max_references` 是模型声明的契约，**这条链上没人
        # 校验它**——生图那条链在 `ImageService._resolve_references` 里拦，
        # 统一门面是自己开的口子，绕过去就等于没有上限。
        # 超量的参考图会被驱动静默截断（只算前 N 张），用户以为 5 张都生效了。
        references = tuple(url for url in request.reference_urls if url.strip())
        limit = max(0, spec.max_references)
        if references and limit == 0:
            raise BadRequest(
                f"模型 {spec.label} 不支持参考图（纯文生图），请换一个支持图生图 / 多图合成的模型"
            )
        if len(references) > limit:
            raise BadRequest(
                f"模型 {spec.label} 最多接受 {limit} 张参考图，本次传了 {len(references)} 张"
            )

        job = DriverJob(
            name=(request.name.strip() or request.prompt.strip()[:60] or "统一生成"),
            prompt=request.prompt,
            # 落 **id** 而不是调用方传进来的原串：路由支持按 label（中文名）解析，
            # 但原串透传给上游只会 404「不认这个模型」（agnes/bailian 都如此）。
            model=spec.id,
            capability=spec.capability.value,
            params_json=encode_request(params, references),
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
        """给下游挑模型用：过滤 `internal=True`。

        统一门面**仍然**接受内部模型 id（替换链可以从这里提交，它自己带
        `archiveRunId`）；只是不主动把它们列进「可选清单」，否则下游按清单
        选一个、提交时被「调用档案标识不匹配」挡回来，错得毫无线索。
        """
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
                max_references=spec.max_references,
            )
            for spec in get_model_router().public_models(wanted)
        )

    def drivers(self) -> tuple[DriverSummary, ...]:
        return driver_summaries()

    # ---- 替换链支持：模型层的事不留给 modules/template 自己做 ----

    def replacement_channels(self) -> tuple[ReplacementChannelInfo, ...]:
        ensure_drivers_registered()
        return channels.replacement_channels()

    def model_for_channel(self, channel: str) -> str:
        return channels.model_for_channel(channel)

    def channel_label(self, channel: str) -> str:
        return channels.channel_label(channel)

    def submit_mode(self, model: str) -> str | None:
        return channels.submit_mode(model)

    async def prepare_inputs(self, model: str, run_id: str) -> None:
        ensure_drivers_registered()
        await channels.prepare_inputs(model, run_id)

    def libtv_paths(self) -> LibTVPaths:
        """执行器资产位置由本模块给出，模板库只经端口问路。"""
        from modules.model_driver.libtv.process import RUNTIME, VENDOR
        from modules.model_driver.libtv.state import RUN_LOCK

        return LibTVPaths(vendor=VENDOR, bridge=RUNTIME, run_lock=RUN_LOCK)

    async def libtv_execute_stage(self, run_id: str, stage: str) -> ExecutorOutcome:
        from modules.model_driver.libtv import executor

        result = await executor.execute_stage(run_id, stage)
        return ExecutorOutcome(code=result.code, message=result.message)
