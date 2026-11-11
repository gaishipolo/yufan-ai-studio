"""并发写入落败时必须是「可重试的冲突」，不能变成 500。

`replacement_runs.state_version` 是 ORM 的 `version_id_col`（见 `models.py`）：任何一次
`UPDATE` 都带 `WHERE state_version = <读到的值>`，输的一方抛 `StaleDataError`。
这条机制是「迟到的失败不能覆盖成功」的基础，但它把一种**日常**情况也带进来了：

    用户点「删除」的同时，轮询正在写进度

这不是故障，是两条正常写入撞在一起。`StaleDataError` 继承 `SQLAlchemyError`，
原来会落到通用数据库异常处理里变成 500「数据库操作失败」，并打一条 `logger.exception`
——对用户是「服务坏了」，对值班是「半夜告警查一段没有 bug 的代码」。

本文件盯三件事：
1. 回收站这类幂等写入撞车后自己重读再写一次，用户看到 200（不是先让他刷新再点一次）。
2. 真的写不进去时是 409 + 可重试提示，不是 500。
3. 状态写入成功之后，投影（`run.json`）失败不能反过来把这次成功报成失败。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests_template_helpers import (
    API,
    add_run,
    drop_run,
    patch_run,
    read_run,
    workspace,
)

from db.session import get_session_factory
from infra.archive.local import get_archive_settings
from modules.template.pipeline import ReplacementStore
from modules.template.repository import RunRepository


@pytest.fixture
def archived(tmp_path, monkeypatch):
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))

    async def _make(**fields) -> str:
        run = await add_run(**fields)
        workspace(run.id, {"id": run.id, "owner_id": run.owner_id, "status": run.status})
        return run.id

    return _make


def _bump_version_once(run_id: str, **fields):
    """造一个「另一个写入者刚刚提交过」的现场：只成功一次，第二次不再插手。"""
    state = {"done": False}

    async def _hook() -> None:
        if state["done"]:
            return
        state["done"] = True
        await patch_run(run_id, **fields)

    return _hook


async def test_recycle_retries_after_a_concurrent_state_write(
    client: TestClient, archived, monkeypatch
) -> None:
    """删除撞上轮询写进度：重读一次就够，用户不该看到错误。"""
    uid = await archived(status="running", framework_status="running")
    url = f"{API}/templates/replacement-runs/{uid}"
    original = RunRepository.save
    race = _bump_version_once(uid, progress_percent=42)

    async def save(self, run):
        await race()
        return await original(self, run)

    monkeypatch.setattr(RunRepository, "save", save)
    response = client.delete(url)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["deletedAt"] is not None
    row = await read_run(uid)
    assert row is not None and row.deleted_at is not None
    # 重试读的是新版本，不能把撞车那次读到的旧字段写回去
    assert row.progress_percent == 42
    await drop_run(uid)


async def test_lost_optimistic_write_is_a_conflict_not_a_server_error(monkeypatch) -> None:
    """写不进去（连着两次撞车）时必须是 409，而不是 500 + 报警日志。"""
    from core.exceptions import Conflict
    from modules.template.replacement_service import ReplacementService

    uid = (await add_run(status="running", framework_status="running")).id
    original = RunRepository.save
    calls = {"count": 0}

    async def save(self, run):
        calls["count"] += 1
        await patch_run(uid, progress_percent=calls["count"])
        return await original(self, run)

    monkeypatch.setattr(RunRepository, "save", save)
    try:
        async with get_session_factory()() as session:
            with pytest.raises(Conflict):
                await ReplacementService(session).set_deleted(uid, "u_001", True)
        assert calls["count"] >= 2, "撞车后必须真的重试过，而不是直接把 500 抛给用户"
    finally:
        await drop_run(uid)


async def test_projection_failure_cannot_fail_a_committed_state_write(monkeypatch) -> None:
    """数据库已经提交，投影（共享盘不可达）失败只该留日志，不该冒泡。"""
    from core.exceptions import UpstreamError
    from modules.template import worker

    uid = (await add_run(status="running", framework_status="running")).id

    def explode(run) -> bool:
        raise UpstreamError("归档根未挂载")

    monkeypatch.setattr(worker, "sync_projection", explode)
    try:
        async with get_session_factory()() as session:
            store = ReplacementStore(session)
            await store.load(uid)
            assert await store.mark_progress(uid, 30) is True
        row = await read_run(uid)
        assert row is not None and row.progress_percent == 30
    finally:
        await drop_run(uid)


async def test_projection_reports_busy_when_the_lock_path_is_unusable(tmp_path) -> None:
    """工作区不可写（共享盘掉了、路径被文件占住）按「忙」返回 False，不是抛 OSError。

    调用方（`prepare_upload`）把 False 解释成「先别碰这份档案，退避后再来」；
    OSError 会顺着队列作业冒泡成一次失败，把一条还没提交的任务记成 `service_error`。
    """
    from modules.template import worker

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    run = await add_run(status="uploaded")
    try:
        assert worker._sync_out(run, blocker / "afile") is False
    finally:
        await drop_run(run.id)


async def test_retry_counter_survives_a_concurrent_progress_write(monkeypatch) -> None:
    """下载重试计数撞上轮询写进度：重读一次继续，不能把下载作业送进死信。"""
    from modules.template import worker

    uid = (await add_run(status="generated", framework_status="running")).id
    original = RunRepository.save
    race = _bump_version_once(uid, progress_percent=77)

    async def save(self, run):
        await race()
        return await original(self, run)

    monkeypatch.setattr(RunRepository, "save", save)
    try:
        async with get_session_factory()() as session:
            delay = await worker._bump_retry(session, uid, reason="成片下载失败")
        assert delay == worker.backoff_delay(1)
        row = await read_run(uid)
        assert row is not None and row.retry_count == 1
        assert row.status == "generated", "计数写入撞车不该把任务判成 service_error"
    finally:
        await drop_run(uid)


async def test_retry_counter_never_breaks_the_chain_under_constant_contention(monkeypatch) -> None:
    """持续撞车（极端情况）也只丢一次计数，不能变成死信或异常。"""
    from modules.template import worker

    uid = (await add_run(status="generated", framework_status="running")).id
    original = RunRepository.save

    async def save(self, run):
        await patch_run(uid, progress_percent=(run.progress_percent or 0) + 1)
        return await original(self, run)

    monkeypatch.setattr(RunRepository, "save", save)
    try:
        async with get_session_factory()() as session:
            delay = await worker._bump_retry(session, uid, reason="成片下载失败")
        assert delay == worker.backoff_delay(1)
        row = await read_run(uid)
        assert row is not None and row.status == "generated"
    finally:
        await drop_run(uid)


async def test_late_receipt_survives_a_takeover_that_gave_up_on_the_submission(
    archived,
) -> None:
    """接管方判死之后，真正的任务号仍然必须写回去。

    现场：A 提交时进程崩在「上游已受理 → 写库」之间；租约过期后 B 接管，
    看到 `submit_attempts>0` 且无 `task_id`，按防重复计费的规则判成
    `submission_unknown + framework_status=failed`。随后 A 拿到了 task_id。
    此时如果补回执也被终态闸门挡住，付费任务就既没有任务号、也没人轮询，
    用户收到的是「可能已计费，请人工核对」——比终态被改更糟。
    """
    run_id = await archived(
        status="submission_unknown",
        framework_status="failed",
        submit_attempts=1,
        configuration={"channel": "bailian"},
    )
    try:
        async with get_session_factory()() as session:
            store = ReplacementStore(session)
            assert await store.mark_submitted(run_id, "bailian:task-abc123") is True
            await session.commit()

        stored = await read_run(run_id)
        assert stored is not None
        assert stored.task_id == "task-abc123"
        assert stored.status == "running"
        assert stored.framework_status == "running"
        assert stored.submit_attempts == 0
    finally:
        await drop_run(run_id)


async def test_receipt_still_cannot_resurrect_a_finished_run(archived) -> None:
    """穿透的只是 `framework_status` 的终态标记，业务状态已完成的行仍然不许动。"""
    run_id = await archived(status="success", framework_status="success")
    try:
        async with get_session_factory()() as session:
            store = ReplacementStore(session)
            assert await store.mark_submitted(run_id, "bailian:task-late") is False

        stored = await read_run(run_id)
        assert stored is not None
        assert stored.status == "success"
        assert stored.task_id is None
    finally:
        await drop_run(run_id)
