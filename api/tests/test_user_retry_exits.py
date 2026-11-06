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
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 显式导入共享夹具供 pytest 注册（归档根目录切到 tmp_path）
from test_replacement_archive import archive_root  # noqa: F401

from core.config import settings
from db.session import get_session_factory
from generation.spec import STATUS_FAILED
from modules.image.models import ImageJob
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask
from modules.template.replacement_repository import ReplacementManifest, ReplacementRepository

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


async def _image_exit(client: TestClient, _root: Path) -> None:
    """`image`：带 confirmCharged 的 retry 必须放行已受理的失败记录。"""
    job_id = await _insert_unknown_image(_me_id(client))
    try:
        resp = client.post(f"{API}/images/{job_id}/retry?confirmCharged=true")
        assert resp.status_code == 200, resp.text
    finally:
        await _drop_image(job_id)


async def _video_exit(client: TestClient, _root: Path) -> None:
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


def _make_unknown_run(root: Path, owner: str) -> str:
    """造一条「进程在供应商受理与写库之间被杀」的档案。"""
    repo = ReplacementRepository()
    run_id = uuid.uuid4().hex
    path = root / "runs/2026/11/06" / f"character-{run_id}"
    for folder in ("inputs", "outputs", "records", "review", "code"):
        (path / folder).mkdir(parents=True, exist_ok=True)
    repo.save(
        path,
        ReplacementManifest(
            id=run_id,
            kind="character",
            target="出口用例",
            owner_id=owner,
            fingerprint="exit-check",
            status="submission_unknown",
            submit_attempts=1,
        ),
    )
    repo.index(path, run_id)
    return run_id


async def _replacement_exit(client: TestClient, root: Path) -> None:
    """`template_replacement`：reconfirm 必须能把死锁记录放回队列。"""
    run_id = _make_unknown_run(root, _me_id(client))
    with pytest.MonkeyPatch.context() as patch:
        # schedule() 在 worker 关闭时拒绝入队（只读实例不该真去提交），
        # 这里要验的是「状态机放行」，所以把 worker 打开。
        patch.setattr(settings, "worker_enabled", True)
        resp = client.post(
            f"{API}/templates/replacement-runs/{run_id}/reconfirm", json={"confirmCharged": True}
        )
    assert resp.status_code == 200, resp.text

    meta = ReplacementRepository().load(ReplacementRepository().path(run_id))
    assert meta.submit_attempts == 0, "出口没清提交意图，恢复器仍会把它定格"
    assert meta.status == "queued"


EXIT_CASES: dict[str, Callable[[TestClient, Path], Awaitable[None]]] = {
    "image": _image_exit,
    "template_replacement": _replacement_exit,
    "video": _video_exit,
}


@pytest.mark.parametrize("kind", sorted(EXIT_CASES))
async def test_declared_exit_actually_works(
    kind: str, client: TestClient, archive_root: Path  # noqa: F811
) -> None:
    await EXIT_CASES[kind](client, archive_root)


async def test_reconfirm_defaults_to_refusal(
    client: TestClient, archive_root: Path  # noqa: F811
) -> None:
    """不带 confirmCharged 时必须 409：一次误点就是第二次计费。"""
    run_id = _make_unknown_run(archive_root, _me_id(client))
    resp = client.post(f"{API}/templates/replacement-runs/{run_id}/reconfirm", json={})
    assert resp.status_code == 409, resp.text
    assert "可能已产生费用" in resp.json()["message"]

    meta = ReplacementRepository().load(ReplacementRepository().path(run_id))
    assert meta.status == "submission_unknown", "拒绝时不能动状态"
    assert meta.submit_attempts == 1, "拒绝时不能清提交意图"
