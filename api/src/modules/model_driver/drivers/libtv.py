"""LibTV 驱动：视频模型（自动载入，不写 `AI_PROVIDERS`）。

整体迁移后本模块同时拥有模型声明、Provider **和**执行器：
`modules/model_driver/libtv/` 里是监督进程、状态模型与固定版 Node 适配器，
子进程以 `-m modules.model_driver.libtv.process` 启动，`runtime/vendor`
按模块自身位置定位。

模板库（`modules/template`）不许直接 import 业务模块，所以它经
`core/ports/model_generation` 拿 `execute_stage` / `VENDOR` / `RUN_LOCK`；
本文件是唯一直接调执行器的地方。
"""

from __future__ import annotations

import asyncio

from core.exceptions import BadRequest, UpstreamError
from infra import get_queue
from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
    ModelSpec,
)
from infra.queue.base import Job
from modules.model_driver.libtv import executor
from modules.model_driver.spec import DriverMeta

META = DriverMeta(
    label="LibTV",
    description="Wan 3.0 替换模板专用（归档输入 + 免费/积分队列）",
    version="1.0.0",
    author="",
)

MODEL_ID = "libtv-wan3-replacement"

# 这些故障需要修复执行环境，再由「继续」恢复已有 task_id 的查询。
EXECUTOR_BLOCKED = frozenset(
    {
        "STALE_LOCK",
        "LOCK_CLEANUP_FAILED",
        "EXECUTOR_IO_FAILED",
        "EXECUTOR_CRASHED",
        "EXECUTOR_ENVIRONMENT_BLOCKED",
    }
)

MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id=MODEL_ID,
        label="LibTV · 720P 替换模板专用",
        provider="libtv",
        capability=Capability.VIDEO,
        description="需先在替换模板归档输入；支持 0 积分畅用队列与积分加速队列",
        # 替换链专用：submit 要求 params 里带 archiveRunId，通用视频页
        # 选中它必然 400。不进模型目录，但 provider_for() 仍能按 id 解析。
        internal=True,
    ),
)


class LibTVProvider:
    name = "libtv"
    capabilities = frozenset({Capability.VIDEO})
    # 返回任务 ID、由框架续轮询；恢复器按缺省 False 处理，必须显式声明
    supports_poll = True
    models = MODELS

    async def submit(self, request: GenerationRequest) -> str:
        value = request.params.get("archiveRunId")
        if not isinstance(value, str) or value != request.request_id:
            raise BadRequest("此模型需从人物/产品/场景替换模板使用")
        path = await asyncio.to_thread(executor.run_path, value)
        previous = await asyncio.to_thread(executor.state, path)
        if previous.task_id:
            return f"{value}:{previous.task_id}"
        if previous.status == "submission_unknown":
            raise UpstreamError("提交回执未知，禁止重提，请先核对上游记录")
        if previous.status != "uploaded":
            raise BadRequest("输入尚未完成上传审核，请先继续准备")
        result = await executor.execute_stage(value, "submit")
        latest = await asyncio.to_thread(executor.state, path)
        if latest.task_id:
            return f"{value}:{latest.task_id}"
        if latest.status in ("submitting", "submission_unknown"):
            raise UpstreamError("提交回执未知，禁止自动重提")
        if latest.status == "waiting_free":
            raise BadRequest("当前报价非零，等待免费；未提交生成")
        if latest.status == "service_error" and latest.last_error:
            # 适配器给了具体原因（积分队列仍报 0、估价不可用等），原样透出。
            raise BadRequest(f"{latest.last_error}；未提交生成")
        if result.code in ("EXECUTOR_BUSY", "STALE_LOCK"):
            # 两种「没提上去」的原因要分开：账号被别的任务占着是等一等，
            # 遗留锁是要人去核对档案，混成一句话会把人引向错误的动作。
            raise BadRequest(f"{result.message}；未提交生成")
        raise BadRequest(f"提交前检查未完成：{result.message or result.code}；未提交生成")

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        run_id, _, task_id = external_id.partition(":")
        if not task_id:
            raise BadRequest("LibTV 上游任务标识无效")
        path = await asyncio.to_thread(executor.run_path, run_id)
        meta = await asyncio.to_thread(executor.state, path)
        if meta.status != "success":
            result = await executor.execute_stage(run_id, "poll")
            if result.code != "OK":
                if result.code in EXECUTOR_BLOCKED:
                    return GenerationProgress(
                        external_id=external_id,
                        state=GenerationState.FAILED,
                        error=(
                            f"LibTV 查询中断：{result.message or result.code}；"
                            "上游任务未取消，处理后请点继续查询"
                        ),
                        retryable=False,
                    )
                raise UpstreamError(f"LibTV 查询暂不可用：{result.message or result.code}")
            meta = await asyncio.to_thread(executor.state, path)
        if meta.status == "generated":
            # 下载是独立非计费工作，不占框架每次查询的 60 秒预算。
            await get_queue().enqueue(
                Job(
                    name="libtv.download",
                    payload={"id": run_id},
                    idempotency_key=f"libtv:download:{run_id}",
                    max_attempts=1,
                    timeout_sec=300,
                )
            )
        status = (
            GenerationState.SUCCEEDED
            if meta.status == "success"
            else GenerationState.FAILED
            if meta.status in ("failed", "timeout")
            else GenerationState.RUNNING
        )
        return GenerationProgress(
            external_id=external_id,
            state=status,
            progress=100 if status == GenerationState.SUCCEEDED else min(99, meta.progress_percent),
            output_url=f"/api/v1/templates/replacement-runs/{run_id}/output"
            if status == GenerationState.SUCCEEDED
            else None,
            error="LibTV 生成失败，请查看调用档案" if status == GenerationState.FAILED else None,
        )

    async def cancel(self, external_id: str) -> None:
        raise BadRequest("当前适配器不自动取消上游任务，请到 LibTV 核对")


Driver = LibTVProvider
