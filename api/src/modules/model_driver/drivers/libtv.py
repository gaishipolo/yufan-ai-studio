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
from pathlib import Path

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
from modules.model_driver.generic_inputs import materialize
from modules.model_driver.libtv import executor
from modules.model_driver.spec import DriverMeta

META = DriverMeta(
    label="LibTV",
    description="Wan 3.0 视频替换（归档输入 + 免费/积分队列）",
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

_PARAM_SCHEMA = {
    # 键名与 `modules/task/params.py` 对齐：前端按 paramSchema 渲染，不加代码。
    "durationSec": {"type": "int", "min": 2, "max": 30, "step": 1, "default": 5},
    "aspectRatio": {
        "type": "enum",
        "options": ["9:16", "16:9", "1:1"],
        "default": "9:16",
    },
}

MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id=MODEL_ID,
        label="LibTV · 720P 视频替换",
        provider="libtv",
        capability=Capability.VIDEO,
        description="720P 视频替换；支持 0 积分畅用队列与积分加速队列",
        param_schema=_PARAM_SCHEMA,
        max_references=5,
        # 替换链的模型：有原片 + 素材包的档案工作区，能按模板做替换。
        replacement=True,
    ),
)


def _request_ready(path: Path) -> bool:
    """`inputs/request.json` 是 upload 阶段的产物，submit 阶段必须读它。"""
    return (path / "inputs/request.json").is_file()


class LibTVProvider:
    name = "libtv"
    capabilities = frozenset({Capability.VIDEO})
    # 返回任务 ID、由框架续轮询；恢复器按缺省 False 处理，必须显式声明
    supports_poll = True
    models = MODELS
    # 通用入口要在本跳里先跑 `upload` 再跑 `submit`（模板库那条链把 upload
    # 排成独立的 prepare job，这里没有）。监督进程给的两个阶段预算是
    # 1500s + 100s；框架会再加 30s 余量。写小了会在上传素材时被队列硬超时
    # 取消，而那时「提交意图」已经落库 → 被判成「可能已计费」。
    submit_timeout_sec = 1650.0

    async def submit(self, request: GenerationRequest) -> str:
        # 归档工作区 id 只能是字符串：模板库那条链把它放在 `archiveRunId`，
        # 通用链没有这个键（None → ""），于是用任务 id 当工作区 id。
        value = str(request.params.get("archiveRunId") or "")
        generic = value != request.request_id
        if generic:
            # 通用生成入口没有模板库写的档案：补出同款工作区，后面的提交、
            # 回执、去重与计费纪律全部照旧。模板库带 archiveRunId 时不进这里。
            await materialize(request)
            value = request.request_id
        path = await asyncio.to_thread(executor.run_path, value)
        if generic and not await asyncio.to_thread(_request_ready, path):
            # `upload` 只做「传素材 + 估价」，不向上游下单；`submit` 才是花钱
            # 的那一步。缺了它，桥接层会读不到 `inputs/request.json`，报一个
            # 和用户输入完全无关的 ENOENT。
            upload = await executor.execute_stage(value, "upload")
            if upload.code != "OK":
                raise BadRequest(f"素材上传准备未完成：{upload.message or upload.code}")
        previous = await asyncio.to_thread(executor.state, path)
        if previous.task_id:
            return f"{value}:{previous.task_id}"
        if previous.status == "submission_unknown":
            raise UpstreamError("提交回执未知，禁止重提，请先核对上游记录")
        # upload 阶段可能停在「等免费」或「适配器报错」：这时不许再调 submit，
        # 否则桥接层只会抛 "Refusing duplicate or uncertain submission"。
        if generic and previous.status == "waiting_free":
            raise BadRequest("当前报价非零，等待免费；未提交生成")
        if generic and previous.status == "service_error" and previous.last_error:
            raise BadRequest(f"{previous.last_error}；未提交生成")
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
