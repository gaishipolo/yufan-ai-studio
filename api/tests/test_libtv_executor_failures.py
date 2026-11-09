"""执行器故障必须留下回执，并停止无效查询重试。"""

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.exceptions import UpstreamError
from infra.ai import libtv, libtv_process
from infra.ai.base import GenerationState
from infra.ai.libtv_state import ExecutorResult, LibTVState


@pytest.mark.parametrize("failure", [PermissionError, RuntimeError])
def test_cleanup_failure_still_saves_finished_receipt(monkeypatch, tmp_path, failure):
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


def test_initialization_failure_returns_actionable_receipt(monkeypatch, tmp_path):
    def denied():
        raise PermissionError("private credential must not appear")

    monkeypatch.setattr(libtv_process, "get_archive_storage", denied)
    result = libtv_process.supervise("poll", tmp_path, "node")
    assert result.code == "EXECUTOR_IO_FAILED"
    assert "private credential" not in result.message


@pytest.mark.parametrize("code", ["STALE_LOCK", "LOCK_CLEANUP_FAILED", "EXECUTOR_IO_FAILED"])
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
