"""周期任务扩展点（#4）。

框架原来只能表达两件事：「请求来了做什么」和「队列入队后做什么」，
都不能表达「没人触发、但要定期发生」。生图这一路撞上的是
「进程内队列重启即丢 → 记录永远 queued」，所以这一维是被真实故障逼出来的。
"""

from __future__ import annotations

import asyncio

import pytest

from core.scheduler import (
    PeriodicJob,
    Scheduler,
    periodic,
    periodic_jobs,
    reset_periodic_jobs,
)


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    reset_periodic_jobs()
    yield
    reset_periodic_jobs()


def _job(
    name: str, every: float, *, immediate: bool = False, sink: list[str] | None = None
) -> PeriodicJob:
    calls = sink if sink is not None else []

    async def handler() -> None:
        calls.append(name)

    return PeriodicJob(name=name, every=every, handler=handler, immediate=immediate)


async def test_immediate_job_runs_on_first_tick() -> None:
    sink: list[str] = []
    scheduler = Scheduler([_job("image.reconcile", 300, immediate=True, sink=sink)])
    scheduler.start(now=1000.0)

    assert await scheduler.run_once(1000.0) == ["image.reconcile"]
    assert sink == ["image.reconcile"]


async def test_plain_job_waits_one_interval() -> None:
    """不写 immediate 就不能在启动瞬间跑 —— 否则重启风暴会把下游打穿。"""
    sink: list[str] = []
    scheduler = Scheduler([_job("task.daily", 60, sink=sink)])
    scheduler.start(now=1000.0)

    assert await scheduler.run_once(1000.0) == []
    assert await scheduler.run_once(1059.0) == []
    assert await scheduler.run_once(1060.0) == ["task.daily"]
    assert sink == ["task.daily"]


async def test_job_runs_again_after_interval() -> None:
    sink: list[str] = []
    scheduler = Scheduler([_job("image.reconcile", 10, immediate=True, sink=sink)])
    scheduler.start(now=0.0)

    assert await scheduler.run_once(0.0) == ["image.reconcile"]
    assert await scheduler.run_once(5.0) == []
    assert await scheduler.run_once(10.0) == ["image.reconcile"]
    assert len(sink) == 2


async def test_failing_job_does_not_block_others() -> None:
    """一个 handler 抛异常不能让同一轮的其他任务饿死，也不能让调度器退出。"""
    ran: list[str] = []

    async def boom() -> None:
        raise RuntimeError("上游挂了")

    async def ok() -> None:
        ran.append("ok")

    scheduler = Scheduler(
        [
            PeriodicJob("image.boom", 60, boom, immediate=True),
            PeriodicJob("image.ok", 60, ok, immediate=True),
        ]
    )
    scheduler.start(now=0.0)

    executed = await scheduler.run_once(0.0)
    assert executed == ["image.ok"]
    assert ran == ["ok"]


async def test_serve_stops_on_event() -> None:
    sink: list[str] = []
    scheduler = Scheduler([_job("image.reconcile", 3600, immediate=True, sink=sink)])
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler.serve(stop))
    await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert sink == ["image.reconcile"]


def test_registration_validates_shape() -> None:
    with pytest.raises(ValueError, match="every"):
        periodic("image.too_frequent", every=0)

    with pytest.raises(ValueError, match="<模块>.<动作>"):
        periodic("noprefix", every=10)


def test_image_module_registers_its_job() -> None:
    """生图模块必须自己把 reconcile 注册上——这条是「模块拥有周期任务」的验收点。"""
    import importlib

    from modules.image import maintenance

    # 其他用例会清空注册表；reload 才是「模块被 import 时注册」的真实路径
    importlib.reload(maintenance)

    names = {job.name for job in periodic_jobs()}
    assert "image.reconcile" in names


async def test_image_reconcile_redispatches_only_stale_rows(
    client: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生图这一路的具体验收：卡住的重新投递，正在跑的别碰。

    `client` 只为确保应用与库已初始化（session 级夹具），本身不参与断言。
    """
    from datetime import timedelta

    from db.base import utcnow
    from db.session import get_session_factory
    from generation.spec import STATUS_QUEUED
    from modules.image import maintenance
    from modules.image.models import ImageJob
    from modules.image.pipeline import KIND_IMAGE

    async with get_session_factory()() as session:
        stale = ImageJob(
            name="卡住的",
            prompt="p",
            status=STATUS_QUEUED,
            owner_id="u_001",
            # 阈值由供应商超时推导，不再是模块级常量（R11-02 现象 C）
            created_at=utcnow() - timedelta(seconds=maintenance._stale_after_sec() + 60),
        )
        fresh = ImageJob(name="刚提交的", prompt="p", status=STATUS_QUEUED, owner_id="u_001")
        session.add_all([stale, fresh])
        await session.commit()
        stale_id, fresh_id = stale.id, fresh.id

    dispatched: list[tuple[str, str]] = []

    class FakeRunner:
        async def dispatch(self, kind: str, record_id: str) -> str:
            dispatched.append((kind, record_id))
            return "job"

    monkeypatch.setattr(maintenance, "get_runner", lambda: FakeRunner())

    await maintenance.reconcile_stale_jobs()

    assert (KIND_IMAGE, stale_id) in dispatched
    assert (KIND_IMAGE, fresh_id) not in dispatched
