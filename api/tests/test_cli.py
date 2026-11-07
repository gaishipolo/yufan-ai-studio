"""命令行入口的冷启动行为。

**为什么要用子进程测**：这里出过的问题恰恰是「导入顺序」——`core.permissions.catalog`
是模块声明时填的，而 CLI 不走 `create_app()`。在同一个测试进程里，前面的用例早就
把模块发现跑过了，catalog 是满的，`sync-roles --prune-snapshot` 看起来完全正常；
换成一个干净的 CLI 进程，catalog 是空集，那条命令会把整张快照抹成 `[]`，
下一次启动于是把管理员手工移除的权限全部当「新增」补回来。

同一个判断在进程内永远测不出来，只能起一个真进程。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]


def _run(
    *args: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **(extra_env or {})}
    return subprocess.run(
        [sys.executable, "-m", "cli", *args],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
        check=False,
    )


def test_prune_snapshot_in_a_cold_process_keeps_the_catalog() -> None:
    """`--prune-snapshot --dry-run` 在干净进程里不得报出「移除 N 个权限点」。

    用 dry-run 是因为：这条用例要验的是**读到的目录对不对**，不是写库；
    真跑一次会在测试库上与父进程抢写锁，收益还不大。
    """
    result = _run("sync-roles", "--prune-snapshot", "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert "快照移除" not in result.stdout, (
        "CLI 里权限目录是空的：`sync-roles` 忘了先 `discover_modules()`，"
        "`--prune-snapshot` 会把整张快照抹掉。输出：\n" + result.stdout + result.stderr
    )
    # 目录真被读到了才会有这句（或者有「+权限点」）
    assert "内置角色已是最新" in result.stdout or "快照补入" in result.stdout


def test_meta_command_lists_keys() -> None:
    """`app meta` 是 `app_meta` 的读入口，至少得能读到权限目录快照。"""
    result = _run("meta")

    assert result.returncode == 0, result.stderr
    assert "permissions.catalog" in result.stdout
    assert "v" in result.stdout, "没有打印版本号"


def test_meta_command_shows_history_for_a_key() -> None:
    """传键时打轨迹：谁、第几版、一句话摘要。"""
    result = _run("meta", "permissions.catalog")

    assert result.returncode == 0, result.stderr
    assert "permissions.catalog" in result.stdout
    assert "最近写入" in result.stdout or "不存在" in result.stdout

# --------------------------------------------------------------------------- #
# app jobs：队列的可读入口（死信看得到、能重投、删不掉不该删的）
# --------------------------------------------------------------------------- #
async def _seed_job(status: str, name: str) -> str:
    """按指定状态塞一行队列记录。字段走 ORM 默认值，免得手写一长串列。"""
    from db.base import utcnow
    from db.jobs import QueuedJob
    from db.session import get_session_factory

    job_id = f"cli-{status}-{name}"
    async with get_session_factory()() as session:
        session.add(
            QueuedJob(
                id=job_id,
                name=name,
                payload="{}",
                status=status,
                run_at=utcnow(),
                attempts=3,
                max_attempts=3,
                last_error="重试次数用尽" if status == "dead" else "",
            )
        )
        await session.commit()
    return job_id


async def _forget_job(job_id: str) -> None:
    from sqlalchemy import delete

    from db.jobs import QueuedJob
    from db.session import get_session_factory

    async with get_session_factory()() as session:
        await session.execute(delete(QueuedJob).where(QueuedJob.id == job_id))
        await session.commit()


def test_jobs_lists_and_redrives_a_dead_letter() -> None:
    """死信原来只能看一个数字，要处理只能自己写 SQL——这条命令替掉那次手写。"""
    import asyncio

    job_id = asyncio.run(_seed_job("dead", "cli.dead"))
    try:
        listed = _run("jobs", "--state", "dead", "--json", extra_env={"QUEUE_DRIVER": "db"})
        assert listed.returncode == 0, listed.stderr
        assert job_id in listed.stdout, "死信没出现在列表里：" + listed.stdout
        assert "重试次数用尽" in listed.stdout, "死信没带出错原因"

        done = _run("jobs", "--redrive", job_id, extra_env={"QUEUE_DRIVER": "db"})
        assert done.returncode == 0, done.stderr
        assert "attempts 归零" in done.stdout

        still_dead = _run("jobs", "--state", "dead", "--json", extra_env={"QUEUE_DRIVER": "db"})
        assert job_id not in still_dead.stdout
        pending = _run("jobs", "--state", "pending", "--json", extra_env={"QUEUE_DRIVER": "db"})
        assert job_id in pending.stdout, "重投后没回到 pending"
    finally:
        asyncio.run(_forget_job(job_id))


def test_jobs_refuses_to_redrive_an_inflight_job() -> None:
    """在途任务不能被「重投」：那等于人手伸进 worker 的状态机，让它被执行两遍。"""
    import asyncio

    job_id = asyncio.run(_seed_job("pending", "cli.pending"))
    try:
        result = _run("jobs", "--redrive", job_id, extra_env={"QUEUE_DRIVER": "db"})
        assert result.returncode == 1
        assert "不需要重投" in result.stdout
    finally:
        asyncio.run(_forget_job(job_id))


def test_jobs_refuses_to_drop_a_running_job() -> None:
    """`running` 可能是别的实例正在干的活，删了那个 worker 不会知道。"""
    import asyncio

    job_id = asyncio.run(_seed_job("running", "cli.running"))
    try:
        no_force = _run("jobs", "--drop", job_id, extra_env={"QUEUE_DRIVER": "db"})
        assert no_force.returncode == 1
        assert "--force" in no_force.stdout

        forced = _run("jobs", "--drop", job_id, "--force", extra_env={"QUEUE_DRIVER": "db"})
        assert forced.returncode == 1, "running 行被删掉了：" + forced.stdout
        assert "拒绝删除" in forced.stdout
    finally:
        asyncio.run(_forget_job(job_id))


def test_jobs_explains_the_memory_driver() -> None:
    """内存驱动没有可查的队列——要说清楚「为什么查不到」，而不是打印一张空表。"""
    result = _run("jobs", extra_env={"QUEUE_DRIVER": "memory"})
    assert result.returncode == 2
    assert "内存" in result.stdout
