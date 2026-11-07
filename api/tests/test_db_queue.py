"""DB 队列驱动：领取、租约接管、幂等键、死信。

这些用例打的是**真表**（测试库是 sqlite 文件），因为要验的正是「重启不丢」这条
内存队列做不到的性质；用假仓储会把最关键的语义测掉。
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, update

from db.base import utcnow
from db.jobs import JOB_DEAD, JOB_RUNNING, QueuedJob
from db.session import get_session_factory
from infra.queue.base import Job
from infra.queue.db import DbJobQueue
from workers import Worker, register_handler


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


# --------------------------------------------------------------------------- #
# 空转退避（原 T26 的「空转查询按 worker 数叠加」那半段）
# --------------------------------------------------------------------------- #
def test_idle_backoff_doubles_then_caps() -> None:
    """连续空转：0.2 → 0.4 → 0.8 → 1.0（封顶），领到任务立刻回到 0.2。

    队列绝大多数时间是空的，而「空」不需要每 200ms 确认一次。封顶值就是
    「空闲时入队到被取走」的最坏延迟，所以它是一个**显式配置**
    （`QUEUE_IDLE_MAX_INTERVAL_SEC`），不是随手写死的 1 秒。
    """
    queue = DbJobQueue(lease_sec=30, poll_interval_sec=0.2, idle_max_interval_sec=1.0)
    assert queue._next_interval() == 0.2

    intervals = []
    for _ in range(6):
        queue._idle = min(queue._idle + 1, 16)
        intervals.append(queue._next_interval())
    assert intervals == [0.4, 0.8, 1.0, 1.0, 1.0, 1.0]


def test_idle_max_never_denser_than_poll() -> None:
    """封顶小于基础间隔时不许把它当成「更密的轮询」：取两者较大值。"""
    queue = DbJobQueue(lease_sec=30, poll_interval_sec=0.5, idle_max_interval_sec=0.05)
    assert queue._next_interval() == 0.5


def test_idle_backoff_resets_after_a_hit() -> None:
    """领到任务就把空转计数清零，否则忙起来的队列会一直被退避拖着。"""
    asyncio.run(_reset())
    queue = DbJobQueue(lease_sec=30, poll_interval_sec=0.01, idle_max_interval_sec=0.02)
    assert asyncio.run(queue.dequeue(timeout=0)) is None
    assert queue._idle == 1

    asyncio.run(queue.enqueue(Job(name="demo", payload={})))
    assert asyncio.run(queue.dequeue(timeout=0.2)) is not None
    assert queue._idle == 0
    assert queue._next_interval() == 0.01


def test_idle_dequeue_issues_fewer_queries_than_fixed_polling() -> None:
    """空转时查询次数要真的降下来——用指标算，不靠「感觉省了」。

    不退避的话，`timeout=0.3` 的 3 次调用会各查约 30 次（0.01s 间隔）；
    退避后每次调用最多几次。上限取得很宽松（30），因为这是时间相关断言，
    在慢机器上不该假红——它要钉的是「数量级」，不是精确值。
    """
    from infra.queue.db import POLLS

    asyncio.run(_reset())
    queue = DbJobQueue(lease_sec=30, poll_interval_sec=0.01, idle_max_interval_sec=0.05)
    before = POLLS.value(outcome="miss")
    for _ in range(3):
        assert asyncio.run(queue.dequeue(timeout=0.3)) is None
    misses = POLLS.value(outcome="miss") - before
    assert misses < 30, f"空转查询没有退避：{misses} 次"
    assert misses >= 3, "每次 dequeue 至少要查一次库，否则逻辑被跳过了"

# --------------------------------------------------------------------------- #
# 进程内唤醒：退避不是「睡着了叫不醒」
# --------------------------------------------------------------------------- #
def _idle_queue() -> DbJobQueue:
    """间隔压到 1s 封顶，这样「有没有被叫醒」在耗时上分得很开。"""
    return DbJobQueue(
        lease_sec=30, poll_interval_sec=0.01, idle_max_interval_sec=1.0
    )


def test_enqueue_wakes_a_waiting_worker() -> None:
    """空闲 worker 已经在等 1 秒退避，此时入队必须把它叫醒。

    不唤醒的话 `dequeue` 会等满 1 秒才再查一次库。断言用 0.5s 上限：
    它要钉的是「被叫醒」（几十毫秒）与「睡满退避」（1 秒）之间的量级差，
    不是精确耗时。
    """
    import time

    asyncio.run(_reset())
    queue = _idle_queue()

    async def scenario() -> tuple[Job | None, float]:
        queue._idle = 16  # 等价于「空转很久了」，下一轮间隔就是 1s 封顶
        waiting = asyncio.create_task(queue.dequeue(timeout=3.0))
        await asyncio.sleep(0.05)  # 让它真的进到等待里
        started = time.perf_counter()
        await queue.enqueue(Job(name="wake.me", payload={}))
        job = await waiting
        return job, time.perf_counter() - started

    job, waited = asyncio.run(scenario())
    assert job is not None and job.name == "wake.me"
    assert waited < 0.5, f"入队后没被叫醒，等了 {waited:.2f}s（退避是 1s）"


def test_delayed_enqueue_does_not_wake() -> None:
    """`delay > 0` 的任务在 `run_at` 之前不可见，叫醒只会让 worker 白查一次。

    「短 job + 延迟重投」是延迟入队的大户：唤醒它等于把退避省下的查询又还回去。
    断言用**查询次数**而不是耗时：错误唤醒会让 worker 醒一次、抢空、然后按复位后的
    密集间隔狂轮询——耗时照样接近 timeout，从时间上看不出问题。
    """
    from infra.queue.db import POLLS

    asyncio.run(_reset())
    queue = _idle_queue()

    async def scenario() -> tuple[Job | None, int]:
        queue._idle = 16  # 下一轮间隔 = 1s 封顶
        before = POLLS.value(outcome="miss")
        waiting = asyncio.create_task(queue.dequeue(timeout=0.6))
        await asyncio.sleep(0.05)
        await queue.enqueue(Job(name="later", payload={}), delay=5)
        job = await waiting
        return job, POLLS.value(outcome="miss") - before

    job, misses = asyncio.run(scenario())
    assert job is None, "延迟任务不该在 run_at 之前被取走"
    assert misses <= 2, f"延迟入队把 worker 叫醒了：0.6s 内白查了 {misses} 次"

# --------------------------------------------------------------------------- #
# 续租与 fencing：正在跑的长任务不能被「以为你死了」的接替者重跑
# --------------------------------------------------------------------------- #
async def _hand_over(job_id: str) -> None:
    """模拟「另一个实例接手了这条 job」：换掉凭据，租约推到未来。

    不用「把租约推到过去」来模拟：那等于「执行者崩了」，本机的 worker 会立刻
    再把它领回来，测的就不是「别人的活被我停掉」了。
    """
    async with get_session_factory()() as session:
        await session.execute(
            update(QueuedJob)
            .where(QueuedJob.id == job_id)
            .values(
                lease_token="someone-else",
                lease_expires_at=utcnow() + timedelta(seconds=60),
            )
        )
        await session.commit()


def test_renew_extends_the_lease() -> None:
    """续租必须真的把 `lease_expires_at` 往后推——否则等于没续。"""
    asyncio.run(_reset())
    queue = _queue()
    job = Job(name="demo.renew", payload={})
    asyncio.run(queue.enqueue(job))

    claimed = asyncio.run(queue.dequeue(timeout=1.0))
    assert claimed is not None
    assert claimed.lease_token, "领取时必须发一个凭据，否则回执无法设防"

    before = asyncio.run(_row(job.id)).lease_expires_at
    assert asyncio.run(queue.renew(claimed)) is True
    after = asyncio.run(_row(job.id)).lease_expires_at
    assert after > before


def test_renew_reports_false_after_takeover() -> None:
    """租约被接手后，旧 worker 的续租必须返回 False（它该停手了）。"""
    asyncio.run(_reset())
    queue = _queue()
    job = Job(name="demo.renew.lost", payload={})
    asyncio.run(queue.enqueue(job))

    first = asyncio.run(queue.dequeue(timeout=1.0))
    assert first is not None
    asyncio.run(_hand_over(job.id))

    assert asyncio.run(queue.renew(first)) is False
    # 新凭据续得上：同一个方法在两个人手里给出不同答案，靠的就是 token
    second = asyncio.run(_row(job.id))
    assert second is not None and second.lease_token != first.lease_token


def test_stale_ack_cannot_delete_the_new_owner_row() -> None:
    """迟到的 ack 不能删掉新主人正在跑的行。

    这是「重复执行」之外更坏的一种：行被删了，新主人崩掉后这条 job 就**凭空消失**，
    连死信都没有。所以 ack 必须带凭据，不能只看 id。
    """
    asyncio.run(_reset())
    queue = _queue()
    job = Job(name="demo.fence.ack", payload={})
    asyncio.run(queue.enqueue(job))

    first = asyncio.run(queue.dequeue(timeout=1.0))
    assert first is not None
    asyncio.run(_hand_over(job.id))

    asyncio.run(queue.ack(first))

    row = asyncio.run(_row(job.id))
    assert row is not None, "旧 worker 的 ack 把新主人正在跑的行删掉了"
    assert row.status == JOB_RUNNING
    assert row.lease_token == "someone-else"

    # 真正的主人仍然能正常收尾
    row.lease_token = "someone-else"
    asyncio.run(queue.ack(_as_job(row)))
    assert asyncio.run(_row(job.id)) is None


def test_stale_nack_cannot_requeue_a_taken_over_job() -> None:
    """迟到的 nack 不能把新主人正在跑的行改成 pending（那会变成第三次执行）。"""
    asyncio.run(_reset())
    queue = _queue()
    job = Job(name="demo.fence.nack", payload={})
    asyncio.run(queue.enqueue(job))

    first = asyncio.run(queue.dequeue(timeout=1.0))
    assert first is not None
    asyncio.run(_hand_over(job.id))

    asyncio.run(queue.nack(first))

    row = asyncio.run(_row(job.id))
    assert row is not None
    assert row.status == JOB_RUNNING, "旧 worker 的 nack 抢走了任务"
    assert row.attempts == 0
    assert row.lease_token == "someone-else"


def test_requeue_after_nack_clears_the_token() -> None:
    """任务回到 pending 就该交回凭据：否则一条队列里的行还带着「谁的」这种信息。"""
    asyncio.run(_reset())
    queue = _queue()
    job = Job(name="demo.nack.token", payload={})
    asyncio.run(queue.enqueue(job))

    claimed = asyncio.run(queue.dequeue(timeout=1.0))
    assert claimed is not None
    asyncio.run(queue.nack(claimed))
    row = asyncio.run(_row(job.id))
    assert row is not None and row.lease_token is None


def _as_job(row: QueuedJob) -> Job:
    return Job(
        id=row.id,
        name=row.name,
        payload={},
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        lease_token=row.lease_token,
    )


# --------------------------------------------------------------------------- #
# worker 侧：续租由 worker 驱动，租约丢了要停手
# --------------------------------------------------------------------------- #
_SLOW: dict[str, Any] = {"runs": 0, "cancelled": False}


@register_handler("test.lease.slow", override=True)
async def _slow_handler(job: Job) -> None:
    """按 payload 睡指定秒数；被取消时留下痕迹（用来看「有没有真的停手」）。"""
    _SLOW["runs"] += 1
    try:
        await asyncio.sleep(float(job.payload.get("sleep", 2.2)))
    finally:
        _SLOW["cancelled"] = True


def test_worker_keeps_the_lease_alive_while_running() -> None:
    """handler 跑过租约时长时，第二个 worker 不能把任务领走。

    这是租约的原始承诺（「领的人崩了别人才能接手」）在**长任务**上的唯一保障：
    不续租的话，凡是跑得比 `QUEUE_LEASE_SEC` 久的 handler 都会被执行两遍——
    表现是用户收到两份成图、供应商被扣两次费。
    """
    asyncio.run(_reset())
    _SLOW.update(runs=0, cancelled=False)
    queue = DbJobQueue(lease_sec=1, poll_interval_sec=0.01)
    other = DbJobQueue(lease_sec=1, poll_interval_sec=0.01)

    async def scenario() -> Job | None:
        await queue.enqueue(Job(name="test.lease.slow", payload={"sleep": 2.2}))
        worker = Worker(queue, concurrency=1)
        await worker.start()
        try:
            # 1.6s > 租约 1s：不续租的话这时租约已经过期，另一实例必然能领走
            await asyncio.sleep(1.6)
            return await other.dequeue(timeout=0.1)
        finally:
            await worker.stop()

    stolen = asyncio.run(scenario())
    assert stolen is None, "续租没生效：正在跑的任务被第二个 worker 领走了（会执行两遍）"
    assert _SLOW["runs"] == 1


def test_worker_stops_the_handler_when_the_lease_is_lost() -> None:
    """确认自己已被接手时必须停手，不能「装作没看见」继续跑完。

    续租能防止「活人被当成死人」，但反过来（真的被接手了）也得认：
    继续跑下去就是两个 worker 同时写同一份业务数据。
    """
    asyncio.run(_reset())
    _SLOW.update(runs=0, cancelled=False)
    queue = DbJobQueue(lease_sec=1, poll_interval_sec=0.01)
    job = Job(name="test.lease.slow", payload={"sleep": 10})

    async def scenario() -> float:
        await queue.enqueue(job)
        worker = Worker(queue, concurrency=1)
        await worker.start()
        started = time.perf_counter()
        try:
            await asyncio.sleep(0.3)
            await _hand_over(job.id)
            # 下一次续租（≤0.25s 后）必然失败 → handler 应被停掉
            for _ in range(60):
                if _SLOW["cancelled"]:
                    break
                await asyncio.sleep(0.05)
            return time.perf_counter() - started
        finally:
            await worker.stop()

    elapsed = asyncio.run(scenario())
    assert _SLOW["cancelled"], "租约丢了还在继续跑"
    assert elapsed < 3.0, f"停手太慢（{elapsed:.1f}s）：说明是靠 10 秒的 handler 自己结束的"
    assert _SLOW["runs"] == 1


def test_memory_driver_needs_no_lease() -> None:
    """内存驱动不会被别人接手，因此「续租」是无需动作的成功。"""
    from infra.queue.memory import MemoryJobQueue

    queue = MemoryJobQueue()
    assert queue.lease_sec is None
    job = Job(name="demo.memory", payload={})
    asyncio.run(queue.enqueue(job))
    claimed = asyncio.run(queue.dequeue(timeout=0.1))
    assert claimed is not None
    assert asyncio.run(queue.renew(claimed)) is True


def test_both_drivers_implement_the_whole_protocol() -> None:
    """协议长了一个成员，每个驱动都要表态。

    防的是一种**静默失效**：新驱动漏了 `renew` 时，worker 的续租会在第一次调用抛
    `AttributeError`，被 `except Exception` 记成一条 warning 就过去了——表现不是报错，
    而是「长任务照旧被执行两遍」。所以这里不看文档、不看注释，直接对着协议本身点名：
    成员清单从 `JobQueue` 反解，不在测试里另抄一份。
    """
    from infra.queue.base import JobQueue
    from infra.queue.memory import MemoryJobQueue

    members = {name for name in dir(JobQueue) if not name.startswith("_")}
    members |= set(getattr(JobQueue, "__annotations__", {}))
    # `JobQueue` 自身的方法里只有协议声明的那些；少写一个成员就等于少一条保证
    assert {"enqueue", "dequeue", "renew", "ack", "nack", "size", "close"} <= members

    for driver_cls in (DbJobQueue, MemoryJobQueue):
        missing = sorted(name for name in members if not hasattr(driver_cls, name))
        assert not missing, f"{driver_cls.__name__} 没实现协议的 {missing}"
