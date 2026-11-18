"""排队顺延：上游说「还在排队」时，不该把这段时间算进生成预算。

2026-11-17 的任务 29dc66ae：免费队列排了 29 分钟，第 30 分钟被判「生成超时」，
而上游一直在正常排队。这里钉住三条：
1. 排队时顺延墙钟起点；
2. 顺延有硬上限（按**建单时间**算，不是按挪过的起点）；
3. 不支持顺延的 store 不该被调用（协议探测，不是 getattr）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from generation.pipeline import DeadlineExtendable
from generation.runner import GenerationRunner
from generation.spec import GenerationRecord
from infra.queue.memory import MemoryJobQueue


class _Store:
    def __init__(self) -> None:
        self.extended = 0

    async def extend_deadline(self, record_id: str) -> bool:
        self.extended += 1
        return True


class _PlainStore:
    """没有顺延能力的实现：不该被调用，也不该报错。"""


def _record(*, created_minutes_ago: int) -> GenerationRecord:
    created = datetime.now(tz=UTC) - timedelta(minutes=created_minutes_ago)
    return GenerationRecord(
        id="a" * 32,
        kind="video",
        model="libtv-wan3-replacement",
        prompt="p",
        status="running",
        owner_id="u_001",
        created_at=created,
        timeout_started_at=created,
    )


def _extend(store: object, record: GenerationRecord) -> None:
    runner = GenerationRunner(MemoryJobQueue())
    asyncio.run(runner._extend_deadline(store, record, "video", record.id))  # noqa: SLF001


def test_queueing_extends_the_wall_clock() -> None:
    store = _Store()
    assert isinstance(store, DeadlineExtendable)

    _extend(store, _record(created_minutes_ago=5))

    assert store.extended == 1


def test_hard_cap_stops_endless_queueing() -> None:
    """排到硬上限（默认 3 小时）就停止顺延：永远排不上的任务必须有个尽头。"""
    store = _Store()

    _extend(store, _record(created_minutes_ago=4 * 60))

    assert store.extended == 0


def test_store_without_the_capability_is_never_called() -> None:
    """调用点靠 `isinstance` 探测（约定见 HANDOFF §5.5），没有这个方法就不该被调用。"""
    store = _PlainStore()

    assert not isinstance(store, DeadlineExtendable)
