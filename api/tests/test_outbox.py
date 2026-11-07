"""事件 outbox：与业务同事务、补投、退避、至少一次。

关键验收只有一条：**提交成功的事件，即使进程在 publish 之前就崩了，也必须被投出去。**
这条用「只 commit 不 publish，然后再 dispatch」来复现——它正是崩溃后的现场。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, timedelta

import pytest
from sqlalchemy import delete, select, update

from core.config import settings
from core.events import DomainEvent, get_event_bus
from db.base import utcnow
from db.outbox import (
    OUTBOX_DELIVERED,
    OUTBOX_FAILED,
    OUTBOX_PENDING,
    OutboxEvent,
    dispatch_loop,
    dispatch_pending,
    prune_finished,
    record,
)
from db.session import get_session_factory


async def _reset() -> None:
    async with get_session_factory()() as session:
        await session.execute(delete(OutboxEvent))
        await session.commit()


async def _rows() -> list[OutboxEvent]:
    async with get_session_factory()() as session:
        return list((await session.scalars(select(OutboxEvent))).all())


def test_record_is_rolled_back_with_the_business_transaction() -> None:
    """`record()` 不 commit：业务回滚时事件也必须一起消失，否则会发出「没发生的事」。"""
    asyncio.run(_reset())

    async def scenario() -> None:
        async with get_session_factory()() as session:
            await record(session, "demo.ghost", x=1)
            await session.rollback()

    asyncio.run(scenario())
    assert asyncio.run(_rows()) == []


def test_committed_event_survives_crash_before_publish() -> None:
    """提交后不发（模拟进程立刻崩掉）→ 之后 dispatch 仍然投得出去。"""
    asyncio.run(_reset())
    bus = get_event_bus()
    seen: list[dict[str, object]] = []

    async def handler(event: DomainEvent) -> None:
        seen.append(dict(event.payload))

    bus.add("demo.crash", handler)
    try:

        async def commit_only() -> None:
            async with get_session_factory()() as session:
                await record(session, "demo.crash", record_id="t-1", note="中文")
                await session.commit()

        asyncio.run(commit_only())
        # 此刻进程「崩溃」：事件只在表里
        rows = asyncio.run(_rows())
        assert [row.status for row in rows] == [OUTBOX_PENDING]
        assert seen == []

        report = asyncio.run(dispatch_pending())
        assert report.delivered == 1
        assert seen == [{"record_id": "t-1", "note": "中文"}]
        assert [row.status for row in asyncio.run(_rows())] == [OUTBOX_DELIVERED]

        # 已投递的不会被重复投递
        assert asyncio.run(dispatch_pending()).delivered == 0
        assert len(seen) == 1
    finally:
        bus.clear()


def test_failed_delivery_backs_off_instead_of_hot_looping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenBus:
        async def publish(self, name: str, **payload: object) -> int:
            raise RuntimeError("bus down")

    asyncio.run(_reset())
    monkeypatch.setattr("db.outbox.get_event_bus", lambda: BrokenBus())

    async def commit_event() -> None:
        async with get_session_factory()() as session:
            await record(session, "demo.broken")
            await session.commit()

    asyncio.run(commit_event())
    report = asyncio.run(dispatch_pending())
    assert report.failed == 1

    row = asyncio.run(_rows())[0]
    assert row.status == OUTBOX_PENDING
    assert row.attempts == 1
    assert row.last_error.startswith("RuntimeError")
    # 退避：available_at 被推到将来，下一轮不会立刻再试。
    # sqlite 读回来是 naive datetime（列声明了 timezone 也不带 tzinfo），比较前补上
    assert row.available_at.replace(tzinfo=UTC) > utcnow()
    assert asyncio.run(dispatch_pending()).delivered == 0


def test_exhausted_event_is_marked_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenBus:
        async def publish(self, name: str, **payload: object) -> int:
            raise RuntimeError("bus down")

    asyncio.run(_reset())
    monkeypatch.setattr("db.outbox.get_event_bus", lambda: BrokenBus())
    monkeypatch.setattr(settings, "outbox_max_attempts", 1)

    async def commit_event() -> None:
        async with get_session_factory()() as session:
            await record(session, "demo.giveup")
            await session.commit()

    asyncio.run(commit_event())
    asyncio.run(dispatch_pending())

    row = asyncio.run(_rows())[0]
    assert row.status == OUTBOX_FAILED
    assert row.attempts == 1


def test_prune_removes_only_old_finished_events() -> None:
    """终态行要清（否则只增不减），pending 再老也不许清——那是还没发出去的事件。"""
    asyncio.run(_reset())

    async def age(event_id: str, days: float) -> None:
        async with get_session_factory()() as session:
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event_id)
                .values(updated_at=utcnow() - timedelta(days=days))
                .execution_options(synchronize_session=False)
            )
            await session.commit()

    async def record_one(name: str) -> str:
        async with get_session_factory()() as session:
            event_id = await record(session, name)
            await session.commit()
        return event_id

    old_done = asyncio.run(record_one("demo.old.done"))
    asyncio.run(dispatch_pending())
    asyncio.run(age(old_done, 30))

    # 新事件单独投一轮，免得把后面那条「故意留着的 pending」也投出去
    fresh_done = asyncio.run(record_one("demo.fresh.done"))
    asyncio.run(dispatch_pending())
    assert fresh_done != old_done

    # 一条很老但**没投出去**的事件：不许被清理掉
    old_pending = asyncio.run(record_one("demo.old.pending"))
    asyncio.run(age(old_pending, 30))

    removed = asyncio.run(prune_finished())
    assert removed == 1

    remaining = {row.name: row.status for row in asyncio.run(_rows())}
    assert remaining == {"demo.old.pending": OUTBOX_PENDING, "demo.fresh.done": OUTBOX_DELIVERED}


def test_dispatch_jobs_are_registered() -> None:
    """两个框架级周期任务必须真的注册上。没注册的症状是「事件写了但永远不发」。"""
    from core.scheduler import periodic_jobs

    names = {job.name for job in periodic_jobs()}
    assert {"core.outbox", "core.outbox.prune"} <= names


def test_dispatch_loop_respects_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """关掉 outbox 时，周期任务不能偷偷清空历史——它只是不动。"""
    asyncio.run(_reset())
    monkeypatch.setattr(settings, "outbox_enabled", False)

    async def commit_event() -> None:
        async with get_session_factory()() as session:
            await record(session, "demo.off")
            await session.commit()

    asyncio.run(commit_event())
    asyncio.run(dispatch_loop())
    assert [row.status for row in asyncio.run(_rows())] == [OUTBOX_PENDING]
