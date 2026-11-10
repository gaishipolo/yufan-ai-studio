"""替换任务的耗时：起点是「确认提交」，终点是「这一轮停下」。

这一组钉的是**口径**，不是格式：

- 起点不能用 `created_at`（`prepared` 可以放几天再确认），也不能用 `updated_at`
  当终点（评价、回收站、补积分都会推着它走）；
- 重提一轮要把上一轮的起止清掉，否则隔天重试会显示成「耗时 20 小时」；
- 没开始 / 历史行没有时间戳时给 `null`，页面显示「未记录」，不许拿别的列猜。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from tests_template_helpers import ADMIN, API, add_run, drop_run, read_run, workspace

from db.session import get_session_factory
from infra.archive.local import get_archive_settings
from modules.template import worker
from modules.template.models import ReplacementRun
from modules.template.pipeline import ReplacementStore
from modules.template.run_clock import elapsed_seconds, set_status


async def test_old_archive_uses_current_execution_timeout_budget(archived) -> None:
    from generation.runner import GenerationRunner

    run_id, _ = await archived(
        status="running",
        task_id="existing-task",
        created_at=datetime.now(UTC) - timedelta(days=2),
        started_at=datetime.now(UTC),
    )
    try:
        async with get_session_factory()() as session:
            record = await ReplacementStore(session).load(run_id)
        assert record is not None
        assert not GenerationRunner._timed_out(record)
    finally:
        await drop_run(run_id)


async def test_continue_interrupted_query_restarts_budget_and_keeps_task_id(archived) -> None:
    from generation.runner import GenerationRunner

    run_id, _ = await archived(
        status="failed",
        framework_status="failed",
        task_id="existing-task",
        created_at=START,
        started_at=START,
        finished_at=START + timedelta(minutes=10),
        last_error="查询中断",
    )
    try:
        async with get_session_factory()() as session:
            assert await worker.schedule_state(session, run_id, ADMIN) == "poll"
        saved = await read_run(run_id)
        assert saved.task_id == "existing-task"
        assert saved.status == "running"
        assert saved.finished_at is None
        assert saved.last_error is None
        async with get_session_factory()() as session:
            record = await ReplacementStore(session).load(run_id)
        assert not GenerationRunner._timed_out(record)
    finally:
        await drop_run(run_id)


async def test_continue_active_query_does_not_extend_budget(archived) -> None:
    run_id, _ = await archived(
        status="running",
        framework_status="running",
        task_id="existing-task",
        started_at=START,
    )
    try:
        async with get_session_factory()() as session:
            assert await worker.schedule_state(session, run_id, ADMIN) == "poll"
        saved = await read_run(run_id)
        assert saved.started_at.replace(tzinfo=UTC) == START
    finally:
        await drop_run(run_id)


START = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)


@pytest.fixture
def archived(tmp_path, monkeypatch):
    """数据库行 + 工作区目录：详情接口与投影都要两者齐全。"""
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))

    async def _make(**fields) -> tuple[str, object]:
        run = await add_run(**fields)
        path = workspace(run.id, {"id": run.id, "owner_id": run.owner_id, "status": run.status})
        return run.id, path

    return _make


# --------------------------------------------------------------------------- #
# 口径（纯函数，不落库）
# --------------------------------------------------------------------------- #
def test_confirm_starts_and_terminal_status_stops() -> None:
    run = ReplacementRun(status="prepared")

    set_status(run, "queued", now=START)
    assert (run.started_at, run.finished_at) == (START, None)
    # 在途：按「到此刻」算，页面标成「已用」
    assert elapsed_seconds(run, now=START + timedelta(seconds=90)) == 90

    set_status(run, "success", now=START + timedelta(seconds=125))
    assert run.finished_at == START + timedelta(seconds=125)
    # 结束之后不再跟着当前时间涨
    assert elapsed_seconds(run, now=START + timedelta(hours=5)) == 125


def test_retry_restarts_the_clock_instead_of_accumulating() -> None:
    """失败后隔天重试，耗时属于这一次，不是两次相加、也不是从第一次算起。"""
    run = ReplacementRun(status="prepared")
    set_status(run, "queued", now=START)
    set_status(run, "failed", now=START + timedelta(seconds=30))
    assert elapsed_seconds(run, now=START + timedelta(seconds=30)) == 30

    set_status(run, "queued", now=START + timedelta(hours=20))
    assert run.finished_at is None
    assert elapsed_seconds(run, now=START + timedelta(hours=20, seconds=45)) == 45


def test_waiting_states_are_not_the_end() -> None:
    """等额度和等执行器还会继续走，提前落终点会让列表显示一个假的小时长。"""
    run = ReplacementRun(status="prepared")
    set_status(run, "queued", now=START)
    for waiting in ("waiting_free", "waiting_executor", "uploading", "uploaded"):
        set_status(run, waiting, now=START + timedelta(seconds=10))
        assert run.finished_at is None
    assert elapsed_seconds(run, now=START + timedelta(seconds=70)) == 70


def test_no_start_means_no_finish() -> None:
    """历史行两列都空：不拿别的列编一个起止。"""
    run = ReplacementRun(status="prepared")
    set_status(run, "service_error", now=START)
    assert run.started_at is None and run.finished_at is None
    assert elapsed_seconds(run, now=START) is None


def test_terminal_without_finish_is_not_counted_forever() -> None:
    """终态却没有终点（旧数据/回执直改）：显示「未记录」，不按当前时间一直涨。"""
    run = ReplacementRun(status="success", started_at=START)
    assert run.finished_at is None
    assert elapsed_seconds(run, now=START + timedelta(hours=3)) is None


# --------------------------------------------------------------------------- #
# 状态机接线：确认提交真的会落起点，成功/失败真的会落终点
# --------------------------------------------------------------------------- #
async def test_confirming_a_submission_writes_started_at(archived) -> None:
    run_id, _ = await archived(status="prepared")
    try:
        async with get_session_factory()() as session:
            assert await worker.schedule_state(session, run_id, ADMIN) == "prepare"
        row = await read_run(run_id)
        assert row is not None
        assert row.status == "queued"
        assert row.started_at is not None
        assert row.finished_at is None
    finally:
        await drop_run(run_id)


async def test_success_and_failure_write_finished_at(archived) -> None:
    ok_id, _ = await archived(status="running", task_id="upstream-ok", started_at=START)
    bad_id, _ = await archived(status="uploading", started_at=START)
    try:
        async with get_session_factory()() as session:
            store = ReplacementStore(session)
            await store.mark_succeeded(ok_id, output_url=None, cover_url=None)
            await store.mark_failed(bad_id, "上传失败")

        ok = await read_run(ok_id)
        bad = await read_run(bad_id)
        assert ok is not None and bad is not None
        assert (ok.status, ok.finished_at is not None) == ("success", True)
        assert (bad.status, bad.finished_at is not None) == ("service_error", True)
    finally:
        await drop_run(ok_id)
        await drop_run(bad_id)


# --------------------------------------------------------------------------- #
# 出参：列表与详情读的是同一份格式化结果
# --------------------------------------------------------------------------- #
async def test_detail_reports_elapsed_seconds(client: TestClient, archived) -> None:
    finished = START + timedelta(seconds=125)
    run_id, _ = await archived(status="success", started_at=START, finished_at=finished)
    try:
        data = client.get(f"{API}/templates/replacement-runs/{run_id}").json()["data"]
        assert data["elapsedSec"] == 125
        assert data["startedAt"] == "2026-11-09T08:00:00Z"
        assert data["finishedAt"] == "2026-11-09T08:02:05Z"
    finally:
        await drop_run(run_id)


async def test_historical_run_reports_no_elapsed(client: TestClient, archived) -> None:
    """上线前的档案没有可信提交时刻：字段为 null，前端显示「未记录」。"""
    run_id, _ = await archived(status="success")
    try:
        data = client.get(f"{API}/templates/replacement-runs/{run_id}").json()["data"]
        assert data["elapsedSec"] is None
        assert data["startedAt"] is None
        assert data["finishedAt"] is None
    finally:
        await drop_run(run_id)
