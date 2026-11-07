"""DB 队列驱动：领取、租约接管、幂等键、死信。

这些用例打的是**真表**（测试库是 sqlite 文件），因为要验的正是「重启不丢」这条
内存队列做不到的性质；用假仓储会把最关键的语义测掉。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import delete, select, update

from db.base import utcnow
from db.jobs import JOB_DEAD, QueuedJob
from db.session import get_session_factory
from infra.queue.base import Job
from infra.queue.db import DbJobQueue


def _queue() -> DbJobQueue:
    # 轮询间隔压到最小：测试不能靠「等 200ms」过
    return DbJobQueue(lease_sec=30, poll_interval_sec=0.01)


async def _reset() -> None:
    async with get_session_factory()() as session:
        await session.execute(delete(QueuedJob))
        await session.commit()


async def _expire_lease(job_id: str) -> None:
    """把租约推到过去，等价于「领着跑的人崩了」。"""
    async with get_session_factory()() as session:
        await session.execute(
            update(QueuedJob)
            .where(QueuedJob.id == job_id)
            .values(lease_expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()


async def _row(job_id: str) -> QueuedJob | None:
    async with get_session_factory()() as session:
        return await session.scalar(select(QueuedJob).where(QueuedJob.id == job_id))


def test_enqueue_then_dequeue_roundtrip() -> None:
    asyncio.run(_reset())
    queue = _queue()
    job = Job(name="demo.one", payload={"a": 1, "note": "中文"})

    assert asyncio.run(queue.enqueue(job)) == job.id
    claimed = asyncio.run(queue.dequeue(timeout=1.0))
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.name == "demo.one"
    assert claimed.payload == {"a": 1, "note": "中文"}
    # 第一次尝试的失败次数是 0（与内存驱动口径一致）
    assert claimed.attempts == 0

    asyncio.run(queue.ack(claimed))
    assert asyncio.run(queue.size()) == 0
    assert asyncio.run(_row(job.id)) is None


def test_delayed_job_is_not_visible_before_run_at() -> None:
    asyncio.run(_reset())
    queue = _queue()
    job = Job(name="demo.delay", payload={})

    asyncio.run(queue.enqueue(job, delay=30))
    assert asyncio.run(queue.dequeue(timeout=0.05)) is None
    # 在途但不是「可运行」：size 统计的是在途数量
    assert asyncio.run(queue.size()) == 1


def test_idempotency_key_dedupes_inflight_jobs() -> None:
    asyncio.run(_reset())
    queue = _queue()
    first = Job(name="demo.idem", payload={}, idempotency_key="poll:task-1")
    second = Job(name="demo.idem", payload={}, idempotency_key="poll:task-1")

    assert asyncio.run(queue.enqueue(first)) == first.id
    assert asyncio.run(queue.enqueue(second)) == first.id
    assert asyncio.run(queue.size()) == 1


def test_idempotency_key_is_released_after_ack() -> None:
    """ack 之后同一个键可以重投——否则「轮询再来一次」会被历史任务挡住。"""
    asyncio.run(_reset())
    queue = _queue()
    original = Job(name="demo.idem2", payload={}, idempotency_key="poll:task-2")
    asyncio.run(queue.enqueue(original))
    claimed = asyncio.run(queue.dequeue(timeout=1.0))
    assert claimed is not None
    asyncio.run(queue.ack(claimed))

    again = Job(name="demo.idem2", payload={}, idempotency_key="poll:task-2")
    assert asyncio.run(queue.enqueue(again)) == again.id
    assert asyncio.run(queue.size()) == 1


def test_expired_lease_is_reclaimed_with_attempts_bump() -> None:
    """执行者崩溃后任务必须能被别人接手，而不是永久卡在 running。"""
    asyncio.run(_reset())
    queue = _queue()
    job = Job(name="demo.crash", payload={"n": 1})
    asyncio.run(queue.enqueue(job))

    first = asyncio.run(queue.dequeue(timeout=1.0))
    assert first is not None
    # 还没到期：别人领不走
    assert asyncio.run(queue.dequeue(timeout=0.05)) is None

    asyncio.run(_expire_lease(job.id))
    retaken = asyncio.run(queue.dequeue(timeout=1.0))
    assert retaken is not None
    assert retaken.id == job.id
    assert retaken.attempts == 1  # 崩溃算一次尝试


def test_nack_requeues_with_backoff() -> None:
    asyncio.run(_reset())
    queue = _queue()
    job = Job(name="demo.nack", payload={}, max_attempts=3)
    asyncio.run(queue.enqueue(job))
    claimed = asyncio.run(queue.dequeue(timeout=1.0))
    assert claimed is not None

    asyncio.run(queue.nack(claimed))
    # 退避 2 秒：立刻取不到，但仍在途
    assert asyncio.run(queue.dequeue(timeout=0.05)) is None
    assert asyncio.run(queue.size()) == 1
    row = asyncio.run(_row(job.id))
    assert row is not None
    assert row.attempts == 1


def test_exhausted_job_becomes_dead_letter() -> None:
    asyncio.run(_reset())
    queue = _queue()
    job = Job(name="demo.dead", payload={}, max_attempts=1)
    asyncio.run(queue.enqueue(job))
    claimed = asyncio.run(queue.dequeue(timeout=1.0))
    assert claimed is not None

    asyncio.run(queue.nack(claimed))
    assert asyncio.run(queue.size()) == 0
    row = asyncio.run(_row(job.id))
    assert row is not None
    assert row.status == JOB_DEAD
    # 死信不占幂等键，留档但不阻塞重投
    assert row.idempotency_key is None
    assert asyncio.run(queue.stats())["dead"] >= 1


def test_concurrent_claim_guard_is_expressed_by_rowcount() -> None:
    """同一个 job 不会被发两次：第二次领取要么拿不到，要么拿到别的。

    不写「真并发两个 worker」的用例：sqlite 的写锁会让它偶发 `database is locked`，
    变成随机器性能漂移的假失败。这里验的是语义——第一次领走后，同一行不可再领。
    """
    asyncio.run(_reset())
    queue = _queue()
    job = Job(name="demo.race", payload={})
    asyncio.run(queue.enqueue(job))

    first = asyncio.run(queue.dequeue(timeout=0.5))
    again = asyncio.run(queue.dequeue(timeout=0.05))
    assert first is not None
    assert first.id == job.id
    assert again is None
