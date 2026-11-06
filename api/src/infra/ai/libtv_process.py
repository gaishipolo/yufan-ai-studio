"""独立监督进程：持有 OS 租约直至 Node 真正退出，API 重启不会释放活任务的锁。

## 与启动方的契约

参数走 **stdin 的 JSON**，不走位置参数（旧写法是 `sys.argv[1..5]` 顺序对齐，
加一个参数就得两侧同时改，改漏了就是「读串位」这种极难查的错）。

    {"version": 1, "stage": "submit", "run": "/…", "legacyRoot": "/…",
     "node": "node", "result": "/…/operation-xxx.json"}

- `version` 不匹配 → 退出码 2，启动方按「协议不一致」报错，而不是静默等一个空结果；
- 读不到 stdin / JSON 不合法 → 同上；
- 可选参数都有默认值，新增参数不会再要求启动方同步改一遍。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.exceptions import Conflict
from infra.ai.libtv_state import LIBTV_PROCESS_PROTOCOL, ExecutorResult
from infra.archive import ArchiveStorage, get_archive_storage, lease

PROTOCOL_VERSION = LIBTV_PROCESS_PROTOCOL

STAGES = {"upload": 1500, "submit": 100, "poll": 45, "download": 240}
RUNTIME = Path(__file__).parent / "libtv_runtime/bridge.mjs"


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
        raise Conflict("旧锁缺少执行进程记录，需先人工核对")
    previous = ExecutorResult.model_validate(storage.read(statefile))
    if previous.phase == "starting" or (previous.node_pid and pid_alive(previous.node_pid)):
        raise Conflict("原执行进程尚未确认退出，不清理锁")
    lock.unlink()  # 已持有独占 OS 租约，且原进程已退出。


def supervise(stage: str, run: Path, legacy: Path, node: str) -> ExecutorResult:
    if stage not in STAGES:
        raise ValueError("未知执行阶段")
    storage = get_archive_storage()
    result = ExecutorResult(stage=stage)
    try:
        with (
            lease(run / "records/executor.lease"),
            lease(legacy / "libtv/state/workbench-worker.lock"),
        ):
            clear_finished_lock(run, storage)
            code = run / "code/runtime-v2/bridge.mjs"
            code.parent.mkdir(parents=True, exist_ok=True)
            if not code.exists():
                shutil.copyfile(RUNTIME, code)
            result.phase = "starting"
            storage.write(run / "records/executor-result.json", result.model_dump())
            old_error = run / "records/executor-error.json"
            if old_error.exists():
                old_error.rename(run / "records" / f"executor-error-{uuid.uuid4().hex}.json")
            with subprocess.Popen(
                [node, str(code), stage, str(run), str(legacy)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ) as process:
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
                # 子进程已退出且仍持有两把 OS 锁，遗留文件可安全清理。
                (run / "records/worker.lock").unlink(missing_ok=True)
                storage.write(run / "records/executor-result.json", result.model_dump())
                return result
    except Conflict:
        # 不能覆盖另一活进程的 executor-result，也不能删除它的锁。
        return ExecutorResult(
            stage=stage, code="EXECUTOR_BUSY", message="账号执行器正在占用或旧锁需核对"
        )
    except OSError:
        return ExecutorResult(
            stage=stage, code="EXECUTOR_IO_FAILED", message="执行器文件或共享盘不可用"
        )


class Payload(BaseModel):
    """启动方 → 监督进程的参数包。新增参数请给默认值，别动 `version`。"""

    model_config = ConfigDict(populate_by_name=True)

    version: int = PROTOCOL_VERSION
    stage: str
    run: str
    legacy_root: str = Field(alias="legacyRoot")
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
    result = supervise(payload.stage, Path(payload.run), Path(payload.legacy_root), payload.node)
    get_archive_storage().write(Path(payload.result), result.model_dump())


if __name__ == "__main__":
    main()
