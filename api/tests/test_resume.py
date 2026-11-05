"""重启恢复：已提交的任务只能续轮询，不能再次提交。

这是直接关系到计费的一条约束：`external_id` 一旦落库，说明供应商侧任务已经
在跑，重启后重新走 submit 会重复扣费并产出两份视频。
"""

from __future__ import annotations

import asyncio

from db.session import get_session_factory
from infra.queue.memory import MemoryJobQueue
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask
from modules.task.worker import resume_unfinished_tasks


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
        await resume_unfinished_tasks(queue)

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

    # 现场保留：external_id 与进度都不能被清空
    assert submitted.external_id == "echo-video-deadbeef-1700000000"
    assert submitted.progress == 42
    assert submitted.status == TaskStatus.QUEUED.value


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
        await resume_unfinished_tasks(queue)

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
