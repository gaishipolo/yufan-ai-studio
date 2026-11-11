"""共享盘瞬时占用不能把已提交的任务中断，持久错误仍须原样上报。"""

import errno
from pathlib import Path

import pytest

from infra.archive import base


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_atomic_write_retries_transient_replace_with_same_temporary(
    monkeypatch, tmp_path, winerror
):
    target = tmp_path / "executor-result.json"
    base.write_json(target, {"phase": "running"})
    original = Path.replace
    attempts = []
    waits = []

    def temporarily_busy(temporary, destination):
        attempts.append(temporary)
        if len(attempts) < 3:
            assert base.read_json(target) == {"phase": "running"}
            error = PermissionError(errno.EACCES, "temporary SMB sharing conflict")
            error.winerror = winerror
            raise error
        return original(temporary, destination)

    monkeypatch.setattr(Path, "replace", temporarily_busy)
    monkeypatch.setattr(base.time, "sleep", waits.append)
    base.write_json(target, {"phase": "finished"})
    assert base.read_json(target) == {"phase": "finished"}
    assert len(attempts) == 3
    assert len(set(attempts)) == 1
    assert waits == [0.05, 0.1]
    assert not attempts[0].exists()


@pytest.mark.parametrize("guarded", [False, True])
def test_atomic_write_stops_on_persistent_permission_or_guard(monkeypatch, tmp_path, guarded):
    target = tmp_path / "run.json"
    base.write_json(target, {"status": "running"})
    attempts = []
    waits = []
    failure = PermissionError(errno.EACCES, "[safe-delete] denied" if guarded else "denied")
    failure.winerror = 5

    def denied(temporary, destination):
        attempts.append(temporary)
        raise failure

    monkeypatch.setattr(Path, "replace", denied)
    monkeypatch.setattr(base.time, "sleep", waits.append)
    with pytest.raises(PermissionError) as raised:
        base.write_json(target, {"status": "success"})
    assert raised.value is failure
    assert len(attempts) == (1 if guarded else 4)
    assert len(waits) == (0 if guarded else 3)
    assert base.read_json(target) == {"status": "running"}


def test_atomic_write_does_not_retry_unrelated_io_failure(monkeypatch, tmp_path):
    failure = OSError(errno.ENOSPC, "disk full")
    waits = []

    def disk_full(temporary, destination):
        raise failure

    monkeypatch.setattr(Path, "replace", disk_full)
    monkeypatch.setattr(base.time, "sleep", waits.append)
    with pytest.raises(OSError) as raised:
        base.write_json(tmp_path / "run.json", {})
    assert raised.value is failure
    assert not waits
