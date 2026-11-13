"""独立监督进程：持有 OS 租约直至 Node 真正退出，API 重启不会释放活任务的锁。

## 与启动方的契约

参数走 **stdin 的 JSON**，不走位置参数（旧写法是 `sys.argv[1..5]` 顺序对齐，
加一个参数就得两侧同时改，改漏了就是「读串位」这种极难查的错）。

    {"version": 2, "stage": "submit", "run": "/…",
     "node": "node", "result": "/…/operation-xxx.json"}

- `version` 不匹配 → 退出码 2，启动方按「协议不一致」报错，而不是静默等一个空结果；
- 读不到 stdin / JSON 不合法 → 同上；
- 可选参数都有默认值，新增参数不会再要求启动方同步改一遍。

适配器目录（`runtime/vendor`）由本模块按自身位置计算，不出现在参数里：
它属于代码，不属于部署配置；桥接层启动时校验 `vendor/manifest.json` 的哈希。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from core.exceptions import AppError, Conflict
from core.logging import get_logger
from infra.archive import ArchiveStorage, get_archive_storage, lease
from infra.archive.runs import lock_path
from modules.model_driver.libtv.executor import get_libtv_settings
from modules.model_driver.libtv.state import (
    LIBTV_PROCESS_PROTOCOL,
    RUN_LOCK,
    ExecutorResult,
)

PROTOCOL_VERSION = LIBTV_PROCESS_PROTOCOL
logger = get_logger(__name__)

STAGES = {"upload": 1500, "submit": 100, "poll": 45, "download": 240}
RUNTIME = Path(__file__).parent / "runtime/bridge.mjs"
# 固定版适配器已随代码一起入库；监督进程把这个仓库内绝对路径交给桥接层，
# 不再是部署时填的环境变量，也不指向任何外部项目。
VENDOR = RUNTIME.parent / "vendor"

# 账号级串行：同一个 LibTV 账号，同一时刻只允许一个执行器上传或提交。
# 锁落在运行工作区旁的 .locks/（REPLACEMENT_RUNS_ROOT 指向共享盘，跨机器一致），
# 不能再用 storage.resolve()：归档切到 S3 后它没有本地路径，会抛 UpstreamError，
# 表现为每次 upload/submit 都变成 EXECUTOR_FAILED。
ACCOUNT_LOCK_NAME = "account-executor"
# 查询与下载是只读的：不进账号锁，否则同一个人开两个任务轮询就会互相判忙。
ACCOUNT_STAGES = frozenset({"upload", "submit"})


class ExecutorBusy(Conflict):
    message = "账号执行器正在执行其他任务，请稍后继续"


class StaleExecutorLock(Conflict):
    message = "上一次执行未正常退出，遗留锁需核对"


def pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ctypes.get_last_error() != 87  # 无权限时不能推断原进程已退出。
    try:
        code = ctypes.c_ulong()
        if not kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
            return True
        return code.value == 259
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


def clear_finished_lock(run: Path, storage: ArchiveStorage) -> None:
    lock = run / "records/worker.lock"
    if not lock.exists():
        return
    statefile = run / "records/executor-result.json"
    if not statefile.exists():
        raise StaleExecutorLock("旧锁缺少执行进程记录，需先人工核对")
    previous = ExecutorResult.model_validate(storage.read(statefile))
    if previous.phase == "starting" or (previous.node_pid and pid_alive(previous.node_pid)):
        raise StaleExecutorLock("原执行进程尚未确认退出，不清理锁")
    lock.unlink()  # 已持有独占 OS 租约，且原进程已退出。


def run_node(
    stage: str, run: Path, vendor: Path, node: str, storage: ArchiveStorage
) -> ExecutorResult:
    """在已持锁的前提下启动 Node 适配器，返回这一阶段的回执。"""
    result = ExecutorResult(stage=stage)
    code = run / "code/runtime-v2/bridge.mjs"
    code.parent.mkdir(parents=True, exist_ok=True)
    # 每次覆盖：桥接层属于执行语义的一部分，run 目录里残留旧副本会让
    # 「代码改了、行为没变」，排查时极难发现。此处已持 executor 排他锁，无并发写。
    shutil.copyfile(RUNTIME, code)
    if stage == "submit":
        # 最终授权校验（原片仍可用、封装仍已发布）已移到业务层
        # （`modules/template` 的提交入口）在调用执行器之前完成：
        # 那里能查数据库，而这里只有文件系统。凡能走到这一步的调用都已被授权，
        # 这里只留一份「谁批准了这次提交」的凭据给事后排查。
        storage.write(run / "records/call-authorization.json", {"authorized": True})
    result.phase = "starting"
    storage.write(run / "records/executor-result.json", result.model_dump())
    old_error = run / "records/executor-error.json"
    if old_error.exists():
        old_error.rename(run / "records" / f"executor-error-{uuid.uuid4().hex}.json")
    # 桥接层只输出错误码，若把 stderr 丢掉就再也无法定位失败原因。
    # 放 logs/ 而不是 records/：records 会被桥接层的 update() 全量遍历算校验和。
    logs = run / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    errors = logs / "executor-stderr.log"
    env = {**os.environ, "JS_REVERSE_BROWSER_URL": get_libtv_settings().browser_url}
    with (
        errors.open("wb") as stream,
        subprocess.Popen(
            [node, str(code), stage, str(run), str(vendor)],
            stdout=subprocess.DEVNULL,
            stderr=stream,
            env=env,
        ) as process,
    ):
        result.node_pid = process.pid
        result.phase = "running"
        storage.write(run / "records/executor-result.json", result.model_dump())
        try:
            exit_code = process.wait(timeout=STAGES[stage])
            result.code = "OK" if exit_code == 0 else "ADAPTER_FAILED"
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            result.code = "EXECUTOR_TIMEOUT"
        result.phase = "finished"
        # 先保存 Node 已退出的事实；清理钩子可能直接终止监督进程，不能只在内存里改。
        storage.write(run / "records/executor-result.json", result.model_dump())
        # 此时仍持有 OS 租约。Node 正常退出已自行删除 worker.lock，只清理遗留文件。
        try:
            lock = run / "records/worker.lock"
            if lock.exists():
                lock.unlink(missing_ok=True)
        except (Exception, SystemExit) as exc:
            # 清理失败不代表 Node 还活着；必须保存 finished，避免后续误判活锁。
            logger.exception("LibTV 锁清理失败 run=%s stage=%s", run.name, stage)
            result.code = "LOCK_CLEANUP_FAILED"
            result.message = "执行进程已退出，但锁清理受阻；请核对文件权限或运行环境限制"
            storage.write(run / "records/executor-result.json", result.model_dump())
            if isinstance(exc, SystemExit):
                # 只记录失败；守卫要求退出时仍退出，不吞掉信号，也不重试删除。
                raise
        return result


def supervise(stage: str, run: Path, node: str) -> ExecutorResult:
    if stage not in STAGES:
        raise ValueError("未知执行阶段")
    try:
        storage = get_archive_storage()
        with lease(
            run / RUN_LOCK,
            message="同一任务已有执行器在运行，请稍后继续",
            error=ExecutorBusy,
        ):
            clear_finished_lock(run, storage)
            if stage not in ACCOUNT_STAGES:
                return run_node(stage, run, VENDOR, node, storage)
            with lease(
                lock_path(ACCOUNT_LOCK_NAME),
                message="账号执行器正在执行其他任务，请稍后继续",
                error=ExecutorBusy,
            ):
                return run_node(stage, run, VENDOR, node, storage)
    except StaleExecutorLock as exc:
        # 不能覆盖另一活进程的 executor-result，也不能删除它的锁。
        logger.warning("执行器遗留锁需人工核对 run=%s stage=%s", run.name, stage)
        return ExecutorResult(stage=stage, code="STALE_LOCK", message=exc.message)
    except ExecutorBusy as exc:
        return ExecutorResult(stage=stage, code="EXECUTOR_BUSY", message=exc.message)
    except AppError as exc:
        # 这条分支原来把 `MediaCatalog.check_run()` 的失败映射成
        # `AUTHORIZATION_REVOKED`。授权校验已于 2026-11-08 移到业务层
        # （`ReplacementService._assert_still_authorized`，那里能查库），
        # 来源不存在了却继续沿用旧码：残留的 `NotFound`（归档根没挂、
        # 账号锁路径解析不到）会被说成「授权被撤销」，排查方向直接跑偏。
        # 兜底仍然保留——别让异常穿透到 job handler 把任务崩成 failed，
        # 但码要诚实地表示「执行器没跑成，原因看 message」。
        logger.warning(
            "执行器前置检查失败 run=%s stage=%s err=%s: %s",
            run.name,
            stage,
            type(exc).__name__,
            exc.message,
        )
        return ExecutorResult(stage=stage, code="EXECUTOR_FAILED", message=exc.message)
    except OSError:
        logger.exception("LibTV 执行器文件访问失败 run=%s stage=%s", run.name, stage)
        return ExecutorResult(
            stage=stage, code="EXECUTOR_IO_FAILED", message="执行器文件或共享盘不可用"
        )


class Payload(BaseModel):
    """启动方 → 监督进程的参数包。新增参数请给默认值，别动 `version`。"""

    model_config = ConfigDict(populate_by_name=True)

    version: int = PROTOCOL_VERSION
    stage: str
    run: str
    node: str = "node"
    result: str


def main() -> None:
    raw = sys.stdin.buffer.read()
    try:
        payload = Payload.model_validate_json(raw)
    except ValidationError as exc:
        print(f"参数协议不匹配（期望 JSON v{PROTOCOL_VERSION}）：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if payload.version != PROTOCOL_VERSION:
        print(
            f"参数协议版本不支持：进程支持 v{PROTOCOL_VERSION}，收到 v{payload.version}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    # 回执本身就在运行工作区，不依赖 S3 客户端成功初始化。
    # 初始化失败时再次初始化同一个驱动，会把可诊断的错误变成「没有回执」。
    from infra.archive.base import write_json

    result = ExecutorResult(
        stage=payload.stage,
        code="EXECUTOR_CRASHED",
        message=(f"监督进程异常中断；请检查 logs/supervisor-{Path(payload.result).stem}.log"),
    )
    try:
        result = supervise(payload.stage, Path(payload.run), payload.node)
    finally:
        # SystemExit 等终止信号继续传播，但尽可能保留本次操作的独立失败回执。
        write_json(Path(payload.result), result.model_dump())


if __name__ == "__main__":
    main()
