"""执行器故障必须留下回执，并停止无效查询重试。"""

import io
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.exceptions import UpstreamError
from infra.ai import libtv, libtv_process
from infra.ai.base import GenerationState
from infra.ai.libtv_state import ExecutorResult, LibTVState
from infra.archive.base import read_json, write_json


@pytest.mark.parametrize("failure", [PermissionError, RuntimeError])
def test_cleanup_failure_still_saves_finished_receipt(monkeypatch, tmp_path, failure):
    (tmp_path / "records").mkdir()
    (tmp_path / "records/worker.lock").touch()
    saved = []
    storage = SimpleNamespace(write=lambda path, value: saved.append(dict(value)))
    child = SimpleNamespace(pid=123, wait=lambda **kw: 0)
    monkeypatch.setattr(libtv_process.subprocess, "Popen", lambda *a, **kw: nullcontext(child))
    original = Path.unlink

    def deny_lock(path, *args, **kwargs):
        if path.name == "worker.lock":
            raise failure("[safe-delete] threshold reached")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_lock)
    result = libtv_process.run_node("poll", tmp_path, tmp_path, "node", storage)
    assert result.code == "LOCK_CLEANUP_FAILED"
    assert result.phase == "finished"
    assert saved[-1]["phase"] == "finished"
    assert saved[-1]["code"] == "LOCK_CLEANUP_FAILED"


@pytest.fixture
def exited_executor(monkeypatch, tmp_path):
    child = SimpleNamespace(pid=123, wait=lambda **kw: 0)
    monkeypatch.setattr(libtv_process.subprocess, "Popen", lambda *a, **kw: nullcontext(child))
    return SimpleNamespace(read=read_json, write=write_json)


def test_finished_receipt_survives_transient_shared_file_conflict(
    monkeypatch, tmp_path, exited_executor
):
    from infra.archive import base

    original = Path.replace
    conflicts = []

    def busy_finished_receipt(temporary, target):
        value = read_json(temporary)
        if (
            Path(target).name == "executor-result.json"
            and value["phase"] == "finished"
            and not conflicts
        ):
            conflicts.append(temporary)
            error = PermissionError(13, "SMB sharing conflict")
            error.winerror = 5
            raise error
        return original(temporary, target)

    monkeypatch.setattr(Path, "replace", busy_finished_receipt)
    monkeypatch.setattr(base.time, "sleep", lambda _: None)
    result = libtv_process.run_node("poll", tmp_path, tmp_path, "node", exited_executor)
    assert result.code == "OK"
    assert read_json(tmp_path / "records/executor-result.json")["phase"] == "finished"
    assert len(conflicts) == 1


def test_cleanup_termination_keeps_finished_state_and_propagates(
    monkeypatch, tmp_path, exited_executor
):
    lock = tmp_path / "records/worker.lock"
    lock.parent.mkdir()
    lock.touch()
    attempts = []
    phases = []
    original = Path.unlink

    def terminate(path, *args, **kwargs):
        if path == lock:
            attempts.append(path)
            # 模拟守卫终止解释器；此时必须已经记录 Node 真正退出。
            phases.append(read_json(tmp_path / "records/executor-result.json")["phase"])
            raise SystemExit(1)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", terminate)
    with pytest.raises(SystemExit) as error:
        libtv_process.run_node("poll", tmp_path, tmp_path, "node", exited_executor)
    assert error.value.code == 1
    assert phases == ["finished"]
    result = read_json(tmp_path / "records/executor-result.json")
    assert result["phase"] == "finished"
    assert result["code"] == "LOCK_CLEANUP_FAILED"
    assert attempts == [lock]
    assert lock.exists()


def test_missing_worker_lock_does_not_trigger_another_delete(
    monkeypatch, tmp_path, exited_executor
):
    original = Path.unlink

    def unexpected_delete(path, *args, **kwargs):
        if path.name == "worker.lock":
            pytest.fail("Node 已清理锁，不应重复删除")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unexpected_delete)
    result = libtv_process.run_node("poll", tmp_path, tmp_path, "node", exited_executor)
    assert result.code == "OK"
    assert result.phase == "finished"


@pytest.mark.parametrize("failure", [SystemExit, KeyboardInterrupt, RuntimeError])
def test_main_keeps_receipt_without_swallowing_termination(monkeypatch, tmp_path, failure):
    def terminated(*args):
        raise failure(1)

    monkeypatch.setattr(libtv_process, "supervise", terminated)
    payload = json.dumps(
        {
            "version": 2,
            "stage": "poll",
            "run": str(tmp_path),
            "result": str(tmp_path / "operation.json"),
        }
    ).encode()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(payload)))
    with pytest.raises(failure) as error:
        libtv_process.main()
    assert error.value.args == (1,)
    result = read_json(tmp_path / "operation.json")
    assert result["code"] == "EXECUTOR_CRASHED"
    assert result["stage"] == "poll"


@pytest.mark.parametrize("receipt", [False, True])
async def test_guard_interruption_has_actionable_error(monkeypatch, tmp_path, receipt):
    monkeypatch.setattr(libtv, "run_path", lambda _: tmp_path)
    monkeypatch.setattr(libtv, "get_archive_storage", lambda: SimpleNamespace(read=read_json))

    def start(*args, **kwargs):
        def communicate(payload):
            params = json.loads(payload)
            kwargs["stderr"].write(
                b'[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":50,"threshold":50}\n'
            )
            if receipt:
                write_json(
                    Path(params["result"]),
                    ExecutorResult(stage="poll", code="EXECUTOR_CRASHED").model_dump(),
                )

        return SimpleNamespace(communicate=communicate, returncode=1)

    monkeypatch.setattr(libtv.subprocess, "Popen", start)
    result = await libtv.execute_stage("a" * 32, "poll")
    assert result.code == "EXECUTOR_ENVIRONMENT_BLOCKED"
    assert "safe-delete" in result.message
    assert "服务" in result.message
    assert "supervisor-operation-" in result.message
    assert '"count"' not in result.message


async def test_unreadable_supervisor_log_preserves_original_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(libtv, "run_path", lambda _: tmp_path)
    monkeypatch.setattr(
        libtv.subprocess,
        "Popen",
        lambda *a, **kw: SimpleNamespace(communicate=lambda payload: None, returncode=1),
    )
    original = Path.open

    def deny_diagnostics(path, mode="r", *args, **kwargs):
        if path.name.startswith("supervisor-") and mode == "rb":
            raise PermissionError("diagnostic log unreadable")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_diagnostics)
    result = await libtv.execute_stage("a" * 32, "poll")
    assert result.code == "EXECUTOR_CRASHED"
    assert "supervisor-operation-" in result.message


async def test_previous_guard_log_does_not_override_new_crash(monkeypatch, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "supervisor-operation-old.log").write_bytes(
        b"[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]"
    )
    monkeypatch.setattr(libtv, "run_path", lambda _: tmp_path)
    monkeypatch.setattr(
        libtv.subprocess,
        "Popen",
        lambda *a, **kw: SimpleNamespace(communicate=lambda payload: None, returncode=1),
    )
    result = await libtv.execute_stage("a" * 32, "poll")
    assert result.code == "EXECUTOR_CRASHED"


def test_initialization_failure_returns_actionable_receipt(monkeypatch, tmp_path):
    def denied():
        raise PermissionError("private credential must not appear")

    monkeypatch.setattr(libtv_process, "get_archive_storage", denied)
    result = libtv_process.supervise("poll", tmp_path, "node")
    assert result.code == "EXECUTOR_IO_FAILED"
    assert "private credential" not in result.message


@pytest.mark.parametrize(
    "code",
    ["STALE_LOCK", "LOCK_CLEANUP_FAILED", "EXECUTOR_IO_FAILED", "EXECUTOR_ENVIRONMENT_BLOCKED"],
)
async def test_poll_stops_on_executor_fault_without_resubmitting(monkeypatch, tmp_path, code):
    monkeypatch.setattr(libtv, "run_path", lambda _: tmp_path)
    monkeypatch.setattr(libtv, "state", lambda _: LibTVState(id="a" * 32, task_id="upstream"))

    async def failed(*args):
        return ExecutorResult(code=code, stage="poll", message="需要核对执行环境")

    monkeypatch.setattr(libtv, "execute_stage", failed)
    result = await libtv.LibTVProvider().poll("a" * 32 + ":upstream")
    assert result.state == GenerationState.FAILED
    assert not result.retryable
    assert "查询" in result.error
    assert result.external_id.endswith(":upstream")


async def test_missing_receipt_retains_supervisor_stderr(monkeypatch, tmp_path):
    monkeypatch.setattr(libtv, "run_path", lambda _: tmp_path)

    def start(*args, **kwargs):
        def communicate(payload):
            json.loads(payload)
            kwargs["stderr"].write(b"PermissionError: diagnostic marker\n")

        return SimpleNamespace(communicate=communicate, returncode=1)

    monkeypatch.setattr(libtv.subprocess, "Popen", start)
    result = await libtv.execute_stage("a" * 32, "poll")
    assert result.code == "EXECUTOR_CRASHED"
    logs = list((tmp_path / "logs").glob("supervisor-*.log"))
    assert len(logs) == 1
    assert "diagnostic marker" in logs[0].read_text()
    assert "协议不一致" not in result.message


def test_main_saves_initialization_error_without_archive_client(monkeypatch, tmp_path):
    import io
    import sys

    def denied():
        raise PermissionError("cannot initialize archive client")

    monkeypatch.setattr(libtv_process, "get_archive_storage", denied)
    payload = json.dumps(
        {
            "version": 2,
            "stage": "poll",
            "run": str(tmp_path),
            "result": str(tmp_path / "result.json"),
        }
    ).encode()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(payload)))
    libtv_process.main()
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["code"] == "EXECUTOR_IO_FAILED"
    assert result["phase"] == "finished"


async def test_busy_executor_remains_retryable(monkeypatch, tmp_path):
    monkeypatch.setattr(libtv, "run_path", lambda _: tmp_path)
    monkeypatch.setattr(libtv, "state", lambda _: LibTVState(id="a" * 32, task_id="upstream"))

    async def busy(*args):
        return ExecutorResult(code="EXECUTOR_BUSY", stage="poll")

    monkeypatch.setattr(libtv, "execute_stage", busy)
    with pytest.raises(UpstreamError):
        await libtv.LibTVProvider().poll("a" * 32 + ":upstream")


async def test_log_permission_failure_stops_before_starting_process(monkeypatch, tmp_path):
    monkeypatch.setattr(libtv, "run_path", lambda _: tmp_path)

    def denied(*args, **kwargs):
        raise PermissionError("logs denied")

    monkeypatch.setattr(Path, "open", denied)
    result = await libtv.execute_stage("a" * 32, "poll")
    assert result.code == "EXECUTOR_IO_FAILED"


@pytest.mark.parametrize("platform, expected", [("win32", 512), ("linux", 0)])
async def test_supervisor_starts_in_independent_windows_process_group(
    monkeypatch, tmp_path, platform, expected
):
    monkeypatch.setattr(libtv, "run_path", lambda _: tmp_path)
    monkeypatch.setattr(libtv.sys, "platform", platform)
    monkeypatch.setattr(libtv.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    captured = {}

    def start(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(communicate=lambda payload: None, returncode=1)

    monkeypatch.setattr(libtv.subprocess, "Popen", start)
    result = await libtv.execute_stage("a" * 32, "poll")
    assert result.code == "EXECUTOR_CRASHED"
    assert captured.get("creationflags") == expected


def test_browser_unreachable_is_not_reported_as_a_login_problem():
    """CDP 连不上与「没登录」在适配器里都是 LOGIN_REQUIRED，必须分开。

    前者要做的是启动/重启执行器浏览器，后者才是登录。混成一个码，
    用户会去重登一个仍然有效的会话，而真正停掉的浏览器没人管。
    """
    explained = libtv.explain_login_failure(
        ExecutorResult(code="ADAPTER_FAILED", stage="upload"),
        libtv.AdapterError(code="LOGIN_REQUIRED", stage="login", detail="fetch failed"),
    )
    assert explained.code == "EXECUTOR_BROWSER_UNAVAILABLE"
    assert "9226" in explained.message
    assert "start-services.ps1" in explained.message
    assert "不会重复计费" in explained.message
    assert "登录" not in explained.message


def test_poll_browser_outage_does_not_claim_the_task_was_never_submitted():
    explained = libtv.explain_login_failure(
        ExecutorResult(code="ADAPTER_FAILED", stage="poll"),
        libtv.AdapterError(code="LOGIN_REQUIRED", stage="login", detail="fetch failed"),
    )
    assert explained.code == "EXECUTOR_BROWSER_UNAVAILABLE"
    assert "尚未提交" not in explained.message
    assert "恢复查询" in explained.message


def test_missing_login_keeps_the_login_action():
    explained = libtv.explain_login_failure(
        ExecutorResult(code="ADAPTER_FAILED", stage="upload"),
        libtv.AdapterError(
            code="LOGIN_REQUIRED",
            stage="login",
            detail="No LibTV login in the project browser. Run start-browser.ps1 and sign in.",
        ),
    )
    assert explained.code == "EXECUTOR_LOGIN_REQUIRED"
    assert "登录" in explained.message


@pytest.mark.parametrize("stage", ["upload", "poll", "submit"])
def test_other_adapter_errors_are_left_untouched(stage):
    original = ExecutorResult(code="ADAPTER_FAILED", stage=stage, message="审核未通过")
    explained = libtv.explain_login_failure(
        original, libtv.AdapterError(code="UPLOAD_OR_AUDIT_FAILED", stage="upload")
    )
    assert explained is original
    assert explained.code == "ADAPTER_FAILED"
    assert libtv.explain_login_failure(original, None) is original
