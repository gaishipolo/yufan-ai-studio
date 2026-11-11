"""LibTV ModelProvider：只接收归档引用，提交与轮询交给 GenerationRunner。"""

from __future__ import annotations

import asyncio
import json
import os
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
from infra.archive import get_archive_storage, run_dir
from infra.driver_config import DriverSettings, driver_config
from infra.queue.base import Job

logger = get_logger(__name__)
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


class LibTvSettings(DriverSettings):
    """LibTV 驱动的参数。环境变量前缀 `LIBTV_`（键名与迁移前一致）。"""

    model_config = driver_config("LIBTV")

    # node 可执行文件；模板模块编译提示词时也用这个
    node: str = "node"
    # 适配器从已登录的本地浏览器读 Cookie；显式下发，避免依赖 API 进程继承的环境变量。
    browser_url: str = "http://127.0.0.1:9226"


@lru_cache
def get_libtv_settings() -> LibTvSettings:
    return LibTvSettings()


# 不能传给执行器的环境变量。原因很具体：宿主若从带 WorkBuddy 垫片的终端启动，
# `PYTHONPATH` 指向垫片目录，子进程会加载那里的 `sitecustomize`，把 `unlink` 换成
# 「进回收站 + 批量删除守卫」。执行器清理 `records/worker.lock` 时撞上守卫就退出，
# 任务被写成「LibTV 查询中断：safe-delete 安全守卫……」，而上游其实还在正常生成。
# 执行器不需要这些变量：导入路径由 venv 的 .pth 提供，node 也不需要额外的 --require。
_SHIM_ENV_KEYS = frozenset({"PYTHONPATH", "NODE_OPTIONS", "BASH_ENV"})
_SHIM_ENV_PREFIXES = ("CODEBUDDY_SAFE_DELETE",)


def supervisor_env() -> dict[str, str]:
    """执行器子进程的环境：继承宿主，但剥掉会改变删除语义的垫片变量。"""
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in _SHIM_ENV_KEYS and not key.upper().startswith(_SHIM_ENV_PREFIXES)
    }


class Pointer(BaseModel):
    path: str


class AdapterError(BaseModel):
    code: str
    stage: str
    # 适配器给的原始说明。**必须留着**：`LOGIN_REQUIRED` 既可能是「浏览器根本没连上」，
    # 也可能是「浏览器连着但没登录」，两者的下一动作完全不同，只有 detail 能分开。
    detail: str = ""


# CDP 连接层的失败特征（见 vendor 的 canvas-transport：先 GET /json/version，再连 ws）。
_BROWSER_DOWN_HINTS = (
    "fetch failed",
    "CDP discovery",
    "CDP connection",
    "Only a loopback",
    "Unexpected CDP",
)


def explain_login_failure(
    result: ExecutorResult, error: AdapterError | None
) -> ExecutorResult:
    """把登录阶段的粗码分成「浏览器没连上」与「浏览器里没登录」。

    适配器在两种情况下都写 `LOGIN_REQUIRED`（stage=login），但用户要做的事不同：
    前者去启动/重启执行器浏览器（`start-services.ps1 -WithBrowser`），后者才是登录。
    混成一个码的后果已经在生产上出现过：浏览器进程没起来，页面却提示「登录」，
    人去重登一个仍然有效的会话，真正停掉的浏览器一直没人管。
    """
    if error is None or error.code != "LOGIN_REQUIRED":
        return result
    detail = error.detail or error.code
    if any(hint in detail for hint in _BROWSER_DOWN_HINTS):
        # 文案跟着阶段走：upload 阶段还没花钱，说「不会重复计费」；查询/下载阶段
        # 任务已经在上游跑着，得说「恢复查询」而不是假装还没提交。
        if result.stage in ("poll", "download"):
            tail = "启动后会自动恢复查询，上游任务未取消"
        else:
            tail = (
                "在你自己终端运行 start-services.ps1 -WithBrowser 启动后点继续；"
                "尚未提交，不会重复计费"
            )
        return result.model_copy(
            update={
                "code": "EXECUTOR_BROWSER_UNAVAILABLE",
                "message": (
                    f"执行器浏览器未连接（CDP {get_libtv_settings().browser_url}，"
                    f"{detail}）。{tail}"
                ),
            }
        )
    return result.model_copy(
        update={
            "code": "EXECUTOR_LOGIN_REQUIRED",
            "message": "执行器浏览器里没有有效的 LibTV 登录，请在该浏览器登录后点继续",
        }
    )


def run_path(run_id: str) -> Path:
    """索引指针 → 运行工作区目录。

    档案在本地工作区、指针在归档存储，两边分开（见 `infra/archive/runs.py`）：
    执行器要真实目录，而列表/恢复只依赖指针。
    """
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise BadRequest("调用标识无效")
    return run_dir(get_archive_storage(), run_id)


def state(path: Path) -> LibTVState:
    return LibTVState.model_validate(get_archive_storage().read(path / "run.json"))


def read_result(path: Path) -> tuple[ExecutorResult, AdapterError | None]:
    storage = get_archive_storage()
    result = ExecutorResult.model_validate(storage.read(path))
    errorfile = path.parent / "executor-error.json"
    error = (
        AdapterError.model_validate(storage.read(errorfile)) if errorfile.exists() else None
    )
    return result, error


def environment_blocked(logfile: Path) -> bool:
    """只认本次监督日志的明确守卫标记，不向用户透出原始日志或推测普通崩溃原因。"""
    try:
        with logfile.open("rb") as stream:
            # 读取有界尾部，避免把大日志全量载入内存。
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 65536))
            return b"[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]" in stream.read(65536)
    except OSError:
        # 辅助诊断失败不能掩盖原有执行器故障。
        logger.warning("LibTV 监督日志不可读 log=%s", logfile)
        return False


async def execute_stage(run_id: str, stage: str) -> ExecutorResult:
    try:
        return await _execute_stage(run_id, stage)
    except OSError:
        logger.exception("LibTV 阶段文件访问或进程启动失败 run_id=%s stage=%s", run_id, stage)
        return ExecutorResult(
            code="EXECUTOR_IO_FAILED",
            stage=stage,
            message="执行器文件或进程不可用；请检查共享盘、文件权限和运行环境",
        )


async def _execute_stage(run_id: str, stage: str) -> ExecutorResult:
    path = await asyncio.to_thread(run_path, run_id)
    # 适配器目录由监督进程按仓库内位置计算，这里不再接收任何外部路径配置。
    resultfile = path / "records" / f"operation-{uuid.uuid4().hex}.json"
    # 参数走 stdin 的 JSON（带版本号），不再靠位置对齐：新增参数只要给默认值，
    # 启动方不必同步改一遍，也不会出现「读串位」这种错位到别的字段的故障。
    payload = json.dumps(
        {
            "version": LIBTV_PROCESS_PROTOCOL,
            "stage": stage,
            "run": str(path),
            "node": get_libtv_settings().node,
            "result": str(resultfile),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    logdir = path / "logs"
    await asyncio.to_thread(logdir.mkdir, parents=True, exist_ok=True)
    logfile = logdir / f"supervisor-{resultfile.stem}.log"
    with logfile.open("wb") as errors:
        process = await asyncio.to_thread(
            subprocess.Popen,
            [sys.executable, "-m", "infra.ai.libtv_process"],
            # A console Ctrl+C during API restart must not interrupt the durable
            # supervisor before it can write its receipt.
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=errors,
            env={**supervisor_env(), "JS_REVERSE_BROWSER_URL": get_libtv_settings().browser_url},
        )
        # 取消等待不杀监督进程；子进程继承日志句柄，可继续保存回执。
        # Windows 的 uvicorn reload 使用 Selector loop，不支持 asyncio 子进程 API。
        await asyncio.to_thread(process.communicate, payload)
    if process.returncode and await asyncio.to_thread(environment_blocked, logfile):
        logger.error(
            "LibTV 监督进程被安全守卫中断 run_id=%s stage=%s exit=%s log=%s",
            run_id,
            stage,
            process.returncode,
            logfile,
        )
        return ExecutorResult(
            code="EXECUTOR_ENVIRONMENT_BLOCKED",
            stage=stage,
            message=(
                "运行环境的 safe-delete 安全守卫阻止了执行器清理；"
                "请检查 API 服务运行环境及删除操作授权，处理后再继续；"
                f"诊断日志：logs/{logfile.name}"
            ),
        )
    if not resultfile.exists():
        logger.error(
            "LibTV 监督进程未返回回执 run_id=%s stage=%s exit=%s log=%s",
            run_id,
            stage,
            process.returncode,
            logfile,
        )
        return ExecutorResult(
            code="EXECUTOR_CRASHED",
            stage=stage,
            message=(
                f"监督进程未返回回执（退出码 {process.returncode}）；请检查 logs/{logfile.name}"
            ),
        )
    result, error = await asyncio.to_thread(read_result, resultfile)
    if result.code != "OK":
        logger.warning(
            "LibTV 阶段未完成 run_id=%s stage=%s code=%s detail=%s",
            run_id,
            stage,
            result.code,
            error.code if error is not None else None,
        )
        if error is not None and not result.message:
            result.message = error.code
        # 登录阶段的粗码在这里落脚：浏览器没连上 / 没登录，两种下一动作。
        result = explain_login_failure(result, error)
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
            description="需先在替换模板归档输入；支持 0 积分畅用队列与积分加速队列",
            # 替换链专用：submit 要求 params 里带 archiveRunId，通用视频页
            # 选中它必然 400。不进模型目录，但 `provider_for()` 仍能按 id 解析。
            internal=True,
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
        path = await asyncio.to_thread(run_path, run_id)
        meta = await asyncio.to_thread(state, path)
        if meta.status != "success":
            result = await execute_stage(run_id, "poll")
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
