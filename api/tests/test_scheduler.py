"""周期任务扩展点（#4）。

框架原来只能表达两件事：「请求来了做什么」和「队列入队后做什么」，
都不能表达「没人触发、但要定期发生」。生图这一路撞上的是
「进程内队列重启即丢 → 记录永远 queued」，所以这一维是被真实故障逼出来的。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from core.scheduler import (
    PeriodicJob,
    Scheduler,
    handler_timeout,
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


async def test_a_hung_handler_is_cancelled_instead_of_freezing_every_job() -> None:
    """一个 handler 永不返回，不能让同一轮后面的任务跟着停摆。

    原来的 `run_once` 是「顺序 await，没有超时」：`asyncio.sleep(3600)` 或者一次
    卡死的 `pg_dump` 会把这个调度器**永久**占住——outbox 投递、生图补偿、素材 GC
    全部安静地不再运行，日志里一条错都没有（错误只覆盖了「抛出来」，没覆盖
    「回不来」）。这条用例用 5 秒硬上限来抓它：旧实现会直接超时失败。
    """
    ran: list[str] = []

    async def hang() -> None:
        await asyncio.sleep(3600)

    async def ok() -> None:
        ran.append("ok")

    scheduler = Scheduler(
        [
            PeriodicJob("image.hang", 60, hang, immediate=True, timeout=0.05),
            PeriodicJob("image.ok", 60, ok, immediate=True),
        ]
    )
    scheduler.start(now=0.0)

    started = time.monotonic()
    executed = await asyncio.wait_for(scheduler.run_once(0.0), timeout=5)
    assert time.monotonic() - started < 5
    # 超时不算执行成功：不能进 executed，也不能被当成「跑过了」
    assert executed == ["image.ok"]
    assert ran == ["ok"]


async def test_leader_only_lease_call_does_not_block_the_event_loop() -> None:
    """抢租约是同步阻塞调用（文件锁 / S3 条件写），不能按在事件循环上。

    `LEADER_ELECTOR=backend` + `ARCHIVE_DRIVER=s3` 时，`acquire` 里是几次 boto3
    往返。旧写法在协程里直接调它：几百毫秒的 S3 期间，所有 HTTP 请求、队列消费、
    其它周期任务一起停摆，而日志里没有任何异常——现象是「服务偶尔卡一下」，
    排查方向会被引到数据库或网络上。这条用例用一个 100ms 的假阻塞抓它。

    测量方式要小心：ticker 必须**先跑起来**再触发 `run_once`。`create_task` 之后
    没有 await 点的话，任务还没开始，它的第一个时间戳取在阻塞之后，于是阻塞被
    完整漏掉——实测会得到「最大间隔 16ms」这种全绿结果，守卫等于不存在。
    """
    block = 0.1

    class _BlockingLease:
        """结构上满足 LeaderElector：同步 acquire 里做阻塞 IO。"""

        name = "blocking"

        def acquire(self, lease: str, *, ttl: float = 60.0) -> bool:
            time.sleep(block)
            return True

        def release(self, lease: str) -> None:
            return None

    async def noop() -> None:
        return None

    scheduler = Scheduler(
        [PeriodicJob("image.gc", 60, noop, immediate=True, leader_only=True)],
        elector=_BlockingLease(),  # type: ignore[arg-type]
    )
    scheduler.start(now=0.0)

    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.03)  # 让 ticker 进入循环，否则测不到阻塞
    await scheduler.run_once(0.0)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert gaps
    worst = max(gaps)
    assert worst < block / 2, (
        f"抢租约期间事件循环被按住 {worst * 1000:.0f}ms"
        f"（假阻塞 {block * 1000:.0f}ms）：同步的 elector 调用必须挪出事件循环"
    )


def test_handler_timeout_defaults_to_the_lease_ttl() -> None:
    """默认超时必须等于租约 TTL，这两个数不能各说各的。

    租约 TTL 决定「别的实例什么时候可以接手」，handler 超时决定「我们什么时候
    停手」。如果 TTL 更短，一个新实例已经接管了，老 handler 还在写——同一件
    不可逆的事（删素材）被做了第二遍。让它们由同一个函数算，是唯一不会漂移的写法。
    """

    async def handler() -> None:  # pragma: no cover - 只取字段
        return None

    for every in (1.0, 300.0, 3600.0):
        job = PeriodicJob("image.reconcile", every, handler)
        assert handler_timeout(job) == max(60.0, every * 2)

    explicit = PeriodicJob("image.reconcile", 3600.0, handler, timeout=120.0)
    assert handler_timeout(explicit) == 120.0
