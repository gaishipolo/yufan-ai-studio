"""LibTV ModelProvider：只接收归档引用，提交与轮询交给 GenerationRunner。"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import uuid
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from core.exceptions import BadRequest, UpstreamError
from core.logging import get_logger
from infra import get_queue
from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
    ModelSpec,
)
from infra.ai.libtv_state import LIBTV_PROCESS_PROTOCOL, ExecutorResult, LibTVState
from infra.archive import get_archive_storage
from infra.driver_config import DriverSettings, driver_config
from infra.queue.base import Job

logger = get_logger(__name__)
MODEL_ID = "libtv-wan3-replacement"


class LibTvSettings(DriverSettings):
    """LibTV 旧运行时的参数。环境变量前缀 `LIBTV_`（键名与迁移前一致）。"""

    model_config = driver_config("LIBTV")

    # 旧运行时目录：只用于执行替换生成，未配置时 commit 阶段会显式报错
    legacy_root: str = ""
    # node 可执行文件；模板模块编译提示词时也用这个
    node: str = "node"


@lru_cache
def get_libtv_settings() -> LibTvSettings:
    return LibTvSettings()


class Pointer(BaseModel):
    path: str


class AdapterError(BaseModel):
    code: str
    stage: str


def run_path(run_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise BadRequest("调用标识无效")
    storage = get_archive_storage()
    pointer = Pointer.model_validate(storage.read(storage.resolve(f"catalog/runs/{run_id}.json")))
    return storage.resolve(pointer.path)


def state(path: Path) -> LibTVState:
    return LibTVState.model_validate(get_archive_storage().read(path / "run.json"))


def read_result(path: Path) -> tuple[ExecutorResult, str | None]:
    storage = get_archive_storage()
    result = ExecutorResult.model_validate(storage.read(path))
    errorfile = path.parent / "executor-error.json"
    error = (
        AdapterError.model_validate(storage.read(errorfile)).code if errorfile.exists() else None
    )
    return result, error


async def execute_stage(run_id: str, stage: str) -> ExecutorResult:
    path = await asyncio.to_thread(run_path, run_id)
    legacy_root = get_libtv_settings().legacy_root.strip()
    if not legacy_root:
        # 以前这个默认值是 `E:/yufan-ai`（只在那台机器上存在），
        # 现在留空并在真正要用时才报错：报错信息里带配置项名，而不是一个谜之路径
        raise UpstreamError(
            "LibTV 旧运行时未配置：请设置 LIBTV_LEGACY_ROOT 后重试（仅替换生成需要）"
        )
    resultfile = path / "records" / f"operation-{uuid.uuid4().hex}.json"
    # 参数走 stdin 的 JSON（带版本号），不再靠位置对齐：新增参数只要给默认值，
    # 启动方不必同步改一遍，也不会出现「读串位」这种错位到别的字段的故障。
    payload = json.dumps(
        {
            "version": LIBTV_PROCESS_PROTOCOL,
            "stage": stage,
            "run": str(path),
            "legacyRoot": legacy_root,
            "node": get_libtv_settings().node,
            "result": str(resultfile),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    process = await asyncio.to_thread(
        subprocess.Popen,
        [sys.executable, "-m", "infra.ai.libtv_process"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # 取消等待不杀监督进程；它继续持有租约并保存回执，重启后可恢复。
    # Windows 的 uvicorn reload 使用 Selector loop，不支持 asyncio 子进程 API。
    await asyncio.to_thread(process.communicate, payload)
    if not resultfile.exists():
        raise UpstreamError(
            "LibTV 执行进程未返回结果：进程内代码与启动方协议不一致（"
            f"期望 JSON v{LIBTV_PROCESS_PROTOCOL}），或监督进程启动即失败；"
            f"退出码 {process.returncode}"
        )
    result, error = await asyncio.to_thread(read_result, resultfile)
    if result.code != "OK":
        logger.warning(
            "LibTV 阶段未完成 run_id=%s stage=%s code=%s detail=%s",
            run_id,
            stage,
            result.code,
            error,
        )
        if error:
            result.message = error
    return result


class LibTVProvider:
    name = "libtv"
    capabilities = frozenset({Capability.VIDEO})
    # 返回任务 ID、由框架续轮询；恢复器按缺省 False 处理，必须显式声明
    supports_poll = True
    models = (
        ModelSpec(
            id=MODEL_ID,
            label="LibTV · 720P 替换模板专用",
            provider="libtv",
            capability=Capability.VIDEO,
            description="需先在替换模板归档输入，仅允许 0 积分提交",
        ),
    )

    async def submit(self, request: GenerationRequest) -> str:
        value = request.params.get("archiveRunId")
        if not isinstance(value, str) or value != request.request_id:
            raise BadRequest("此模型需从人物/产品/场景替换模板使用")
        path = await asyncio.to_thread(run_path, value)
        previous = await asyncio.to_thread(state, path)
        if previous.task_id:
            return f"{value}:{previous.task_id}"
        if previous.status == "submission_unknown":
            raise UpstreamError("提交回执未知，禁止重提，请先核对上游记录")
        if previous.status != "uploaded":
            raise BadRequest("输入尚未完成上传审核，请先继续准备")
        result = await execute_stage(value, "submit")
        latest = await asyncio.to_thread(state, path)
        if latest.task_id:
            return f"{value}:{latest.task_id}"
        if latest.status in ("submitting", "submission_unknown"):
            raise UpstreamError("提交回执未知，禁止自动重提")
        if latest.status == "waiting_free":
            raise BadRequest("当前报价非零，等待免费；未提交生成")
        if result.code == "EXECUTOR_BUSY":
            raise BadRequest("账号执行器正在占用，请稍后继续；未提交生成")
        raise BadRequest(f"提交前检查未完成：{result.message or result.code}；未提交生成")

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        run_id, _, task_id = external_id.partition(":")
        if not task_id:
            raise BadRequest("LibTV 上游任务标识无效")
        path = await asyncio.to_thread(run_path, run_id)
        meta = await asyncio.to_thread(state, path)
        if meta.status != "success":
            result = await execute_stage(run_id, "poll")
            if result.code != "OK":
                raise UpstreamError(f"LibTV 查询暂不可用：{result.message or result.code}")
            meta = await asyncio.to_thread(state, path)
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
