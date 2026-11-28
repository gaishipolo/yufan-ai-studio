"""重启恢复：已提交的任务只能续轮询，不能再次提交。

这是直接关系到计费的一条约束：`external_id` 一旦落库，说明供应商侧任务已经
在跑，重启后重新走 submit 会重复扣费并产出两份视频。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from db.session import get_session_factory
from generation.recovery import recover_kind
from generation.runner import get_runner
from infra.queue.memory import MemoryJobQueue
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask


def test_resume_resubmits_only_tasks_without_external_id() -> None:
    async def scenario() -> tuple[list[dict], VideoTask, VideoTask]:
        factory = get_session_factory()
        async with factory() as session:
            submitted = VideoTask(
                title="已提交的",
                prompt="雪山",
                status=TaskStatus.RUNNING.value,
                model="Seedance 2.5",
                progress=42,
                external_id="echo-video-deadbeef-1700000000",
                author_id="u_001",
                author_name="老大",
            )
            pending = VideoTask(
                title="未提交的",
                prompt="海浪",
                status=TaskStatus.QUEUED.value,
                model="Seedance 2.5",
                author_id="u_001",
                author_name="老大",
            )
            session.add_all([submitted, pending])
            await session.commit()
            submitted_id, pending_id = submitted.id, pending.id

        queue = MemoryJobQueue()
        await recover_kind("video", runner=get_runner(queue))

        async with factory() as session:
            after_submitted = await session.get(VideoTask, submitted_id)
            after_pending = await session.get(VideoTask, pending_id)
            assert after_submitted is not None and after_pending is not None
            return queue.snapshot(), after_submitted, after_pending

    jobs, submitted, pending = asyncio.run(scenario())
    keys = {job["key"] for job in jobs}

    # 已提交的：只有轮询，没有重新提交
    assert f"poll:video:{submitted.id}" in keys
    assert f"submit:video:{submitted.id}" not in keys
    # 未提交的：正常重新提交
    assert f"submit:video:{pending.id}" in keys
    assert f"poll:video:{pending.id}" not in keys

    # 现场保留：external_id、进度、**以及 running 状态**都不能被改写。
    # 状态退回 queued 会出现「排队中 · 42%」这种自相矛盾的展示（R4-04）
    assert submitted.external_id == "echo-video-deadbeef-1700000000"
    assert submitted.progress == 42
    assert submitted.status == TaskStatus.RUNNING.value


def test_resubmit_resets_stale_display_fields_before_dispatch() -> None:
    """没提交过、却停在 running 的旧记录：派发前必须把现场复位。

    不复位时页面会一直显示「运行中 · 62%」，直到 worker 真正开始跑；
    错误文案与开始时间也会残留到下一轮。
    """

    async def scenario() -> tuple[list[dict], VideoTask]:
        factory = get_session_factory()
        async with factory() as session:
            stale = VideoTask(
                title="停在 running 的",
                prompt="晚霞",
                status=TaskStatus.RUNNING.value,
                progress=62,
                error_message="上一轮的旧错误",
                started_at=datetime.now(tz=UTC) - timedelta(hours=1),
                model="Seedance 2.5",
                author_id="u_001",
                author_name="老大",
            )
            session.add(stale)
            await session.commit()
            stale_id = stale.id

        queue = MemoryJobQueue()
        await recover_kind("video", runner=get_runner(queue))

        async with factory() as session:
            after = await session.get(VideoTask, stale_id)
            assert after is not None
            return queue.snapshot(), after

    jobs, task = asyncio.run(scenario())
    try:
        keys = {job["key"] for job in jobs}

        assert f"submit:video:{task.id}" in keys
        assert task.status == TaskStatus.QUEUED.value
        assert task.progress == 0
        assert task.error_message is None
        assert task.started_at is None
    finally:
        async def cleanup() -> None:
            async with get_session_factory()() as session:
                row = await session.get(VideoTask, task.id)
                if row is not None:
                    await session.delete(row)
                    await session.commit()

        asyncio.run(cleanup())


def test_resume_does_not_resubmit_when_submit_result_unknown() -> None:
    """「供应商已受理 → 落库」之间崩溃：既不能自动重提，也不能装作没事。

    `submit_attempts > 0` 且没有 `external_id` 说明提交动作已经发出去了，
    但结果没落库。此时自动重提 = 第二份账单，所以转成失败等人核对。
    """

    async def scenario() -> tuple[list[dict], VideoTask]:
        factory = get_session_factory()
        async with factory() as session:
            unknown = VideoTask(
                title="提交结果未知的",
                prompt="极光",
                status=TaskStatus.RUNNING.value,
                model="Seedance 2.5",
                submit_attempts=1,
                author_id="u_001",
                author_name="老大",
            )
            session.add(unknown)
            await session.commit()
            unknown_id = unknown.id

        queue = MemoryJobQueue()
        await recover_kind("video", runner=get_runner(queue))

        async with factory() as session:
            after = await session.get(VideoTask, unknown_id)
            assert after is not None
            return queue.snapshot(), after

    jobs, task = asyncio.run(scenario())
    keys = {job["key"] for job in jobs}

    assert f"submit:video:{task.id}" not in keys
    assert f"poll:video:{task.id}" not in keys
    assert task.status == TaskStatus.FAILED.value
    assert "提交结果未知" in (task.error_message or "")


def test_task_submit_mode_snapshot_overrides_current_catalog() -> None:
    """视频恢复与 image 同口径：提交时的形态快照优先，不再看今天的模型目录。"""

    async def scenario() -> tuple[str, str]:
        factory = get_session_factory()
        async with factory() as session:
            sync_orphan = VideoTask(
                title="同步孤儿",
                prompt="雪山",
                status=TaskStatus.RUNNING.value,
                model="Seedance 2.5",
                external_id="sync-task",
                submit_mode="sync",
                author_id="u_001",
                author_name="老大",
            )
            async_running = VideoTask(
                title="异步运行中",
                prompt="海浪",
                status=TaskStatus.QUEUED.value,
                model="Seedance 2.5",
                external_id="async-task",
                submit_mode="async",
                progress=30,
                author_id="u_001",
                author_name="老大",
            )
            session.add_all([sync_orphan, async_running])
            await session.commit()
            return sync_orphan.id, async_running.id

    queue = MemoryJobQueue()
    sync_id, async_id = asyncio.run(scenario())
    try:
        asyncio.run(recover_kind("video", runner=get_runner(queue)))

        async def after() -> tuple[VideoTask | None, VideoTask | None]:
            factory = get_session_factory()
            async with factory() as session:
                return (
                    await session.get(VideoTask, sync_id),
                    await session.get(VideoTask, async_id),
                )

        sync_task, async_task = asyncio.run(after())
        assert sync_task is not None and sync_task.status == TaskStatus.FAILED.value
        assert async_task is not None and async_task.status == TaskStatus.RUNNING.value
        keys = {job["key"] for job in queue.snapshot()}
        assert f"poll:video:{async_id}" in keys
        assert f"submit:video:{async_id}" not in keys
    finally:
        async def cleanup() -> None:
            factory = get_session_factory()
            async with factory() as session:
                for task_id in (sync_id, async_id):
                    task = await session.get(VideoTask, task_id)
                    if task is not None:
                        await session.delete(task)
                await session.commit()

        asyncio.run(cleanup())


def test_reconcile_only_picks_tasks_that_stopped_moving() -> None:
    """周期巡检只捞「很久没有写入」的任务。

    捞全部会让正在正常推进的任务也重排一条 poll job —— 新 job 的 `polls=0`
    会把次数计数打回原点，「轮询次数上限」这条判据就被悄悄废掉了
    （2026-11-19 排查时正是被这条判据误导过）。
    """

    from datetime import UTC, datetime, timedelta

    async def scenario() -> tuple[list[dict], str, str]:
        factory = get_session_factory()
        stale_at = datetime.now(tz=UTC) - timedelta(minutes=20)
        async with factory() as session:
            stalled = VideoTask(
                title="链路断了的",
                prompt="旧任务",
                status=TaskStatus.RUNNING.value,
                model="LibTV · 720P 视频替换",
                external_id="echo-video-stalled",
                author_id="u_001",
                author_name="老大",
                updated_at=stale_at,
            )
            moving = VideoTask(
                title="还在推进的",
                prompt="新任务",
                status=TaskStatus.RUNNING.value,
                model="LibTV · 720P 视频替换",
                external_id="echo-video-moving",
                author_id="u_001",
                author_name="老大",
            )
            session.add_all([stalled, moving])
            await session.commit()
            stalled_id, moving_id = stalled.id, moving.id

        queue = MemoryJobQueue()
        await recover_kind("video", runner=get_runner(queue), stale_after_sec=900)
        return queue.snapshot(), stalled_id, moving_id

    jobs, stalled_id, moving_id = asyncio.run(scenario())
    keys = {job["key"] for job in jobs}

    assert f"poll:video:{stalled_id}" in keys, "20 分钟没写入的任务必须被接回轮询"
    assert f"poll:video:{moving_id}" not in keys, "刚刚还在推进的任务不该被重排"
