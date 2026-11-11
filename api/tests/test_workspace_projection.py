from pathlib import Path

import pytest
from tests_template_helpers import add_run, drop_run

from infra.archive.base import lease
from modules.template import worker, workspace


def test_write_state_failed_atomic_replace_preserves_old_file(tmp_path, monkeypatch):
    workspace.write_state(tmp_path, {"id": "old"})
    original = Path.replace

    def fail(source, target):
        if Path(target) == tmp_path / "run.json":
            raise OSError("replacement failed")
        return original(source, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="replacement failed"):
        workspace.write_state(tmp_path, {"id": "new"})
    assert workspace.read_state(tmp_path) == {"id": "old"}


async def test_projection_cannot_write_during_executor_lease(tmp_path):
    run = await add_run(status="queued")
    workspace.write_state(tmp_path, {"id": run.id, "status": "uploaded"})
    try:
        with lease(tmp_path / "records/executor.lock"):
            assert worker._sync_out(run, tmp_path) is False
        assert workspace.read_state(tmp_path)["status"] == "uploaded"
    finally:
        await drop_run(run.id)


async def test_stale_projection_cannot_erase_accepted_task(tmp_path):
    run = await add_run(status="uploaded")
    state = {"id": run.id, "status": "submitted", "task_id": "accepted"}
    workspace.write_state(tmp_path, state)
    try:
        assert worker._sync_out(run, tmp_path) is False
        assert workspace.read_state(tmp_path) == state
    finally:
        await drop_run(run.id)


async def test_normal_submitted_projection_still_writes(tmp_path):
    run = await add_run(status="running", task_id="accepted")
    workspace.write_state(tmp_path, {"id": run.id, "status": "submitted", "task_id": "accepted"})
    try:
        assert worker._sync_out(run, tmp_path) is True
        assert workspace.read_state(tmp_path)["task_id"] == "accepted"
    finally:
        await drop_run(run.id)


async def test_legacy_projection_cannot_erase_task_without_workspace_id(tmp_path):
    run = await add_run(status="uploaded")
    state = {"status": "uploaded", "task_id": "accepted"}
    workspace.write_state(tmp_path, state)
    try:
        assert worker._sync_out(run, tmp_path) is False
        assert workspace.read_state(tmp_path) == state
    finally:
        await drop_run(run.id)


async def test_projection_cannot_erase_receipt_before_task_state_written(tmp_path):
    import json

    run = await add_run(status="uploaded", submit_attempts=1)
    state = {"id": run.id, "status": "submitting"}
    workspace.write_state(tmp_path, state)
    (tmp_path / "records").mkdir()
    (tmp_path / "records/submission.json").write_text(
        json.dumps({"data": {"taskId": "accepted"}}), encoding="utf-8"
    )
    try:
        assert worker._sync_out(run, tmp_path) is False
        assert workspace.read_state(tmp_path) == state
    finally:
        await drop_run(run.id)


@pytest.mark.parametrize("status", ["generated", "success"])
async def test_projection_cannot_regress_executor_completion(tmp_path, status):
    run = await add_run(status="running", task_id="accepted")
    state = {"id": run.id, "status": status, "task_id": "accepted"}
    workspace.write_state(tmp_path, state)
    try:
        assert worker._sync_out(run, tmp_path) is False
        assert workspace.read_state(tmp_path) == state
    finally:
        await drop_run(run.id)


async def test_projection_rejects_older_database_version(tmp_path):
    run = await add_run(status="uploaded")
    run.state_version = 1
    state = {"id": run.id, "status": "service_error", "state_version": 2}
    workspace.write_state(tmp_path, state)
    try:
        assert worker._sync_out(run, tmp_path) is False
        assert workspace.read_state(tmp_path) == state
    finally:
        await drop_run(run.id)


async def test_projection_writes_newer_database_version(tmp_path):
    run = await add_run(status="uploaded")
    run.state_version = 2
    workspace.write_state(tmp_path, {"id": run.id, "status": "queued", "state_version": 1})
    try:
        assert worker._sync_out(run, tmp_path) is True
        assert workspace.read_state(tmp_path)["state_version"] == 2
    finally:
        await drop_run(run.id)


async def test_absorb_ignores_receipt_from_older_database_version(tmp_path):
    run = await add_run(status="running", task_id="accepted", progress_percent=70)
    run.state_version = 3
    workspace.write_state(tmp_path, {
        "id": run.id, "status": "uploaded", "progress_percent": 0, "state_version": 2,
    })
    try:
        worker._absorb(run, tmp_path)
        assert (run.status, run.progress_percent) == ("running", 70)
    finally:
        await drop_run(run.id)
