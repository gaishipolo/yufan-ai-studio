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

**2026-11-08 重建后**：template 的现场从「工作区里的 run.json」变成
「`replacement_runs` 表里的一行」，出口本身（`/reconfirm`）没变。这个文件被
重写而不是丢弃——它正是发现「`replacement_worker` 这个模块名不存在」的那条
测试：没有它，`execute` 与 `reconfirm` 会在 import 那一行就抛 ImportError，
而全量测试一片绿。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from core.config import settings
from db.session import get_session_factory
from generation.spec import STATUS_FAILED
from infra.archive import get_archive_storage
from infra.archive.runs import relative_key, runs_root, save_pointer
from modules.image.models import ImageJob
from modules.model_driver.models import DriverJob
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask
from modules.template.models import ReplacementRun
from modules.template.workspace import ensure_layout, write_state

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


async def _make_unknown_run(owner: str) -> str:
    """造一条「进程在供应商受理与写库之间被杀」的记录。

    两个现场都要真造：数据库里那行（状态机的真相）+ 工作区与指针（详情页要
    读 `archive_path`，执行器要真实目录）。只造数据库会让断言蓝在 500 上，
    而那是测试自己的问题，不是代码的问题。
    """
    run_id = uuid.uuid4().hex
    async with get_session_factory()() as session:
        session.add(
            ReplacementRun(
                id=run_id,
                kind="character",
                target="出口用例",
                owner_id=owner,
                fingerprint="exit-check",
                status="submission_unknown",
                submit_attempts=1,
            )
        )
        await session.commit()
    path = runs_root() / f"character-{run_id}"
    ensure_layout(path)
    # 执行器协议文件：`run.json` 不在时 `run_dir()` 按「档案不存在」处理（换机器
    # 时的正确表现），详情页也就打不开。这里要验的是状态机，所以把它写上。
    write_state(
        path,
        {
            "id": run_id,
            "kind": "character",
            "owner_id": owner,
            "status": "submission_unknown",
            "submit_attempts": 1,
        },
    )
    save_pointer(get_archive_storage(), run_id, relative_key(path))
    return run_id


async def _drop_run(run_id: str) -> None:
    async with get_session_factory()() as session:
        row = await session.get(ReplacementRun, run_id)
        if row is not None:
            await session.delete(row)
            await session.commit()


async def _replacement_exit(client: TestClient) -> None:
    """`template_replacement`：reconfirm 必须能把死锁记录放回队列。"""
    owner = _me_id(client)
    run_id = await _make_unknown_run(owner)
    try:
        with pytest.MonkeyPatch.context() as patch:
            # schedule() 在 worker 关闭时拒绝入队（只读实例不该真去提交），
            # 这里要验的是「状态机放行」，所以把 worker 打开。
            patch.setattr(settings, "worker_enabled", True)
            resp = client.post(
                f"{API}/templates/replacement-runs/{run_id}/reconfirm",
                json={"confirmCharged": True},
            )
        assert resp.status_code == 200, resp.text
        async with get_session_factory()() as session:
            after = await session.get(ReplacementRun, run_id)
            assert after is not None
            assert after.submit_attempts == 0, "出口没清提交意图，恢复器仍会把它定格"
            assert after.status == "queued"
    finally:
        await _drop_run(run_id)


EXIT_CASES: dict[str, Callable[[TestClient], Awaitable[None]]] = {
    "image": _image_exit,
    "model_driver": _model_driver_exit,
    "template_replacement": _replacement_exit,
    "video": _video_exit,
}


@pytest.mark.parametrize("kind", sorted(EXIT_CASES))
async def test_declared_exit_actually_works(
    kind: str, client: TestClient, example_drivers: None
) -> None:
    await EXIT_CASES[kind](client)


async def test_reconfirm_defaults_to_refusal(client: TestClient) -> None:
    """不带 confirmCharged 时必须 409：一次误点就是第二次计费。"""
    owner = _me_id(client)
    run_id = await _make_unknown_run(owner)
    try:
        resp = client.post(f"{API}/templates/replacement-runs/{run_id}/reconfirm", json={})
        assert resp.status_code == 409, resp.text
        assert "可能已产生费用" in resp.json()["message"]
        async with get_session_factory()() as session:
            after = await session.get(ReplacementRun, run_id)
            assert after is not None
            assert after.status == "submission_unknown", "拒绝时不能动状态"
            assert after.submit_attempts == 1, "拒绝时不能清提交意图"
    finally:
        await _drop_run(run_id)
