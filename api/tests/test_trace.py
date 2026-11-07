"""trace 贯穿：入队快照 → 执行恢复 → 退出还原。

这三步缺任何一步都会让「异步之后日志接不回去」：
- 不快照：job 里根本没有 id；
- 不恢复：worker 的日志仍然只带 `-`；
- 不还原：同一个 worker 的下一条任务会继承上一条的 id，日志里出现幽灵请求。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select

from core import trace
from core.context import get_request_id, set_request_id
from infra.queue.base import Job
from infra.queue.memory import MemoryJobQueue
from workers import HANDLERS, Worker, register_handler


@pytest.fixture(autouse=True)
def _clean_context():
    """contextvar 在同一线程里会残留，每个用例前后都归零。"""
    set_request_id("-")
    yield
    set_request_id("-")


def test_adopt_and_release_roundtrip() -> None:
    assert trace.current() == ""
    previous = trace.adopt("req-1")
    assert trace.current() == "req-1"
    trace.release(previous)
    # 还原后必须回到「没有请求上下文」，否则日志会挂在一条不存在的请求上
    assert trace.current() == ""


def test_adopt_empty_means_no_context() -> None:
    previous = trace.adopt("")
    assert trace.current() == ""
    trace.release(previous)


async def test_memory_queue_stamps_trace_and_worker_restores_it() -> None:
    queue = MemoryJobQueue()
    seen: list[str] = []

    @register_handler("trace.demo")
    async def handler(job: Job) -> None:
        seen.append(get_request_id())

    set_request_id("req-abc123")
    try:
        await queue.enqueue(Job(name="trace.demo", payload={}))
        worker = Worker(queue, concurrency=1)
        await worker.start()
        for _ in range(200):
            if seen:
                break
            await asyncio.sleep(0.01)
        await worker.stop()
    finally:
        HANDLERS.pop("trace.demo", None)

    assert seen == ["req-abc123"], "handler 里的日志拿不到触发它的请求 id"


async def test_db_queue_persists_trace_id(client) -> None:
    """db 驱动的 trace 必须落库：worker 可能在另一个进程里，内存态带不过去。"""
    from db.jobs import QueuedJob
    from db.session import get_session_factory
    from infra.queue.db import DbJobQueue

    queue = DbJobQueue()
    set_request_id("req-db-1")
    job = Job(name="trace.demo.db", payload={})
    try:
        await queue.enqueue(job)
    finally:
        set_request_id("-")

    async with get_session_factory()() as session:
        stored = await session.scalar(
            select(QueuedJob.trace_id).where(QueuedJob.id == job.id)
        )
        await session.execute(delete(QueuedJob).where(QueuedJob.id == job.id))
        await session.commit()
    assert stored == "req-db-1"


async def test_outbox_records_trace_and_restores_it_for_subscribers(client) -> None:
    """outbox 的投递在周期任务里发生：订阅方必须能看到写入时的 request id。"""
    from core.events import get_event_bus
    from db.outbox import OUTBOX_DELIVERED, OutboxEvent, dispatch_pending, record
    from db.session import get_session_factory

    seen: list[str] = []
    bus = get_event_bus()

    @bus.subscribe("trace.demo.event")
    async def _handler(event: object) -> None:
        seen.append(get_request_id())

    set_request_id("req-outbox-1")
    try:
        async with get_session_factory()() as session:
            event_id = await record(session, "trace.demo.event", value=1)
            await session.commit()
    finally:
        set_request_id("-")

    await dispatch_pending()
    assert seen == ["req-outbox-1"]

    # 投递完成后清场，避免污染后续用例的 outbox 计数
    async with get_session_factory()() as session:
        await session.execute(
            delete(OutboxEvent).where(
                OutboxEvent.id == event_id, OutboxEvent.status == OUTBOX_DELIVERED
            )
        )
        await session.commit()
