"""进程内事件总线：优先级排序、失败隔离、两种 publish 的语义差异。

不碰数据库：总线的契约（谁先跑、失败怎么办）与持久化无关。
"""

from __future__ import annotations

import asyncio

import pytest

from core.events import DomainEvent, EventBus, EventDeliveryError


def test_priority_orders_handlers_across_levels() -> None:
    """`priority` 小的先执行 —— 这是 `subscribe` 文档里写下的承诺。

    回归点：`_dispatch` 曾用一把 `asyncio.gather` 并发执行**全部**订阅者，
    排序结果只影响列表顺序，不影响起跑时间：priority 10 的 handler 和默认 100
    的同时开跑，谁先完成取决于 I/O。于是「先写审计再发通知」这类依赖顺序的
    组合会随机出错，而调度器看起来一切正常。
    """
    bus = EventBus()
    order: list[str] = []

    async def first(event: DomainEvent) -> None:
        order.append("first")

    async def middle(event: DomainEvent) -> None:
        order.append("middle")

    async def last(event: DomainEvent) -> None:
        order.append("last")

    bus.add("demo.happened", last, priority=200)
    bus.add("demo.happened", first, priority=10)
    bus.add("demo.happened", middle, priority=100)

    asyncio.run(bus.publish("demo.happened"))

    assert order[0] == "first", "高优先级（数字小）必须先跑完"
    assert order[-1] == "last"


def test_wildcard_subscribers_respect_priority_too() -> None:
    """通配订阅者与具名订阅者一起排序，不能因为列表拼接顺序被排到最后。"""
    bus = EventBus()
    order: list[str] = []

    async def wildcard(event: DomainEvent) -> None:
        order.append("wildcard")

    async def named(event: DomainEvent) -> None:
        order.append("named")

    bus.add("*", wildcard, priority=10)
    bus.add("demo.happened", named, priority=100)

    asyncio.run(bus.publish("demo.happened"))

    assert order == ["wildcard", "named"]


def test_same_priority_still_runs_concurrently() -> None:
    """同 priority 的订阅者并发执行：加一个慢 handler 不该把别人的延迟也拖长。

    用 `Barrier` 判定：两个 handler 都在等对方到场。若实现是同优先级串行，
    第一个会一直等下去，`wait_for` 超时即失败——比断言耗时的浮点比较可靠。
    """

    async def scenario() -> None:
        bus = EventBus()
        barrier = asyncio.Barrier(2)

        async def a(event: DomainEvent) -> None:
            await barrier.wait()

        async def b(event: DomainEvent) -> None:
            await barrier.wait()

        bus.add("demo.happened", a)
        bus.add("demo.happened", b)
        await asyncio.wait_for(bus.publish("demo.happened"), timeout=3)

    asyncio.run(scenario())


def test_failure_stays_isolated_and_does_not_block_later_levels() -> None:
    """一个订阅者失败：记日志、继续跑其余订阅者，`publish` 不上抛。

    跨优先级时这条更要成立——否则高优先级 handler 一挂，
    低优先级的「写审计」也跟着不执行，正好丢掉最需要留痕的那条记录。
    """
    bus = EventBus()
    ran: list[str] = []

    async def boom(event: DomainEvent) -> None:
        raise RuntimeError("通知渠道挂了")

    async def later(event: DomainEvent) -> None:
        ran.append("later")

    bus.add("demo.happened", boom, priority=10)
    bus.add("demo.happened", later, priority=100)

    count = asyncio.run(bus.publish("demo.happened"))

    assert count == 2
    assert ran == ["later"]


def test_strict_mode_still_raises_after_running_everyone() -> None:
    """`publish_strict` 要「全都跑完再抛」：outbox 靠它决定重试，但重试是整条事件级的。"""
    bus = EventBus()
    ran: list[str] = []

    async def boom(event: DomainEvent) -> None:
        raise RuntimeError("投递失败")

    async def ok(event: DomainEvent) -> None:
        ran.append("ok")

    bus.add("demo.happened", boom, priority=10)
    bus.add("demo.happened", ok, priority=100)

    with pytest.raises(EventDeliveryError) as excinfo:
        asyncio.run(bus.publish_strict("demo.happened"))

    assert ran == ["ok"], "有订阅者失败也要把其余的跑完"
    assert len(excinfo.value.failures) == 1
