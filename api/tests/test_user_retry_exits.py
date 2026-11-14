"""每种生成 kind 的「用户自救出口」：按**行为**断言，不按关键字（R14-01）。

`runner` 对「提交结果未知」的记录一律判死，此后 `handle_submit` 的终态早退让它
再也动不了。文案却写着「请核对后手动重试」——所以每个 kind 都必须真有一个入口
能把这种记录放行，否则那条记录永久死锁（R11-02 现象 A / R14-01）。

上一版守卫是三个正则匹配模块源码，其中 `"retry_count" in text` 专为 template 加的：
`retry_count` 计的是**自动**重试，还在被拦下的那一刻被 `schedule_state` 清零，
连「用户点得动」都证明不了。于是守卫对 template 返回 True，而那条记录点一百次
都只会得到同一句 Conflict。

这里的 `EXIT_CASES` 是唯一清单：条目本身就是一次真实调用（造「提交结果未知」
的现场 → 走用户点得到的入口 → 断言不抛 Conflict）。`test_architecture.py` 只对账
「每个 kind 都在这张表里」，不再猜文本。新增 kind 时，先写出口再登记。

**2026-11-14 P3**：`template_replacement` 这条 kind 随替换链下线 —— 它曾经是
唯一一个「已付费任务不许自动判死」的链路，现在替换走普通 `/tasks`，自救出口与
视频链同一条（`/tasks/{id}/retry`）。留下三条：image / model_driver / video。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from db.session import get_session_factory
from generation.spec import STATUS_FAILED
from modules.image.models import ImageJob
from modules.model_driver.models import DriverJob
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask

API = "/api/v1"


def _me_id(client: TestClient) -> str:
    resp = client.get(f"{API}/identity/me")
    assert resp.status_code == 200
    return str(resp.json()["data"]["id"])


async def _insert_unknown_image(owner: str) -> str:
    job = ImageJob(
        name="retry-exit",
        prompt="出口用例",
        status=STATUS_FAILED,
        owner_id=owner,
        model="echo",
        model_id="echo-image",
        external_id="EXT-EXIT-CHECK",
        created_at=datetime.now(tz=UTC) - timedelta(hours=2),
    )
    async with get_session_factory()() as session:
        session.add(job)
        await session.commit()
        return job.id


async def _drop_image(job_id: str) -> None:
    async with get_session_factory()() as session:
        job = await session.get(ImageJob, job_id)
        if job is not None:
            await session.delete(job)
            await session.commit()


async def _image_exit(client: TestClient) -> None:
    """`image`：带 confirmCharged 的 retry 必须放行已受理的失败记录。"""
    job_id = await _insert_unknown_image(_me_id(client))
    try:
        resp = client.post(f"{API}/images/{job_id}/retry?confirmCharged=true")
        assert resp.status_code == 200, resp.text
    finally:
        await _drop_image(job_id)


async def _model_driver_exit(client: TestClient) -> None:
    """`model_driver`：统一生成任务的自救出口，同样要先清提交意图。"""
    actor = _me_id(client)
    job = DriverJob(
        name="retry-exit",
        prompt="出口用例",
        model="md-sync-image",
        capability="image",
        status=STATUS_FAILED,
        owner_id=actor,
        external_id="EXT-MD-EXIT-CHECK",
        submit_attempts=1,
    )
    async with get_session_factory()() as session:
        session.add(job)
        await session.commit()
        job_id = job.id
    try:
        resp = client.post(f"{API}/model-driver/tasks/{job_id}/retry?confirmCharged=true")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "queued"
    finally:
        async with get_session_factory()() as session:
            row = await session.get(DriverJob, job_id)
            if row is not None:
                await session.delete(row)
                await session.commit()


async def _video_exit(client: TestClient) -> None:
    """`video`：失败任务的重试必须清掉提交意图，否则入队即被防线拦下。"""
    actor = _me_id(client)
    task = VideoTask(
        title="retry-exit",
        prompt="出口用例",
        status=TaskStatus.FAILED.value,
        model="Seedance 2.5",
        submit_attempts=1,
        author_id=actor,
        author_name="出口用例",
    )
    async with get_session_factory()() as session:
        session.add(task)
        await session.commit()
        task_id = task.id
    try:
        resp = client.post(f"{API}/tasks/{task_id}/retry")
        assert resp.status_code == 200, resp.text
        async with get_session_factory()() as session:
            after = await session.get(VideoTask, task_id)
            assert after is not None
            assert after.status == TaskStatus.QUEUED.value
            assert after.submit_attempts == 0
    finally:
        async with get_session_factory()() as session:
            row = await session.get(VideoTask, task_id)
            if row is not None:
                await session.delete(row)
                await session.commit()


EXIT_CASES: dict[str, Callable[[TestClient], Awaitable[None]]] = {
    "image": _image_exit,
    "model_driver": _model_driver_exit,
    "video": _video_exit,
}


@pytest.mark.parametrize("kind", sorted(EXIT_CASES))
async def test_declared_exit_actually_works(
    kind: str, client: TestClient, example_drivers: None
) -> None:
    await EXIT_CASES[kind](client)
