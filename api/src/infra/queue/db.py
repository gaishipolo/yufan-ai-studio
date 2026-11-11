"""数据库队列驱动（`QUEUE_DRIVER=db`）。

`memory` 驱动的队列随进程消失，`db` 驱动把 job 写进 `queued_jobs` 表，重启后接着跑。
两份实现共用 `JobQueue` 协议，业务侧永远只拿协议（`infra.get_queue()`）。

## 领取语义（这是本驱动唯一的难点）

`dequeue()` 不允许「先查再改」：两个 worker 同时查到同一行就会各跑一遍，等于重复扣费。
真正的领取是**条件更新**：

```sql
UPDATE queued_jobs SET status='running', lease_expires_at=?
 WHERE id=? AND (status='pending' AND run_at<=now
                 OR status='running' AND lease_expires_at<now)
```

`rowcount == 1` 才算领到，否则说明被人抢先了，重来一轮。这条语句在 sqlite 上由写锁
串行化，在 postgres 上由行锁串行化——**不需要 `SKIP LOCKED`**，因为它不会「跳过」，
只会「抢输了再来」，多副本下也不会重复执行。

## 租约而不是「删了就跑」

领走就把行删掉最省事，但执行者崩溃后那条 job 就**永远消失**（或在途状态永久卡死）。
所以领取只打标记，完成才 `ack`（删行）；`lease_expires_at` 到期后别人可以接手，
连续崩溃 `max_attempts` 次转死信（`status='dead'`）留档。

## 续租与凭据：租约到期到底意味着什么

主形态确实是**短 job + 延迟重投**（`enqueue(job, delay=8)` 轮询，而不是一个 job 跑
20 分钟）。但「handler 一定跑得比租约短」曾经只是一条款式约定：`QUEUE_LEASE_SEC`
默认 300 秒，而单次 handler 的预算是 300 秒、供应商侧单个请求就允许 300 秒——
两个数字本来就贴在一起，任何一次慢调用都会滑过去。

滑过去之后不只是「重跑一遍」：租约到期只说明**没人再证明自己活着**，
不代表持有者已经停手。于是同一个 job 会被两个 worker 同时跑（用户收到两份成图、
供应商被扣两次费），而先跑完的那个 `ack` 还会把行删掉，后跑的那个崩了就彻底丢失。

所以持有关系做成了双向的：

1. **worker 续租**（`renew`）：`workers.py` 在 handler 执行期间按 `lease_sec / 4`
   的节奏续，租约只在「真的没人续」时才过期。到期因此重新等于「执行者崩了」。
2. **凭据（fencing token）**：每次领取写一个新的 `lease_token`，续租/`ack`/`nack`
   都要求它对得上。旧主人只能白跑，**不能**删行、不能把新主人的任务改成 pending。

`lease_token` 为空的历史行（升级那一刻正在跑的 job）退化成「只动 `status='running'`
的行」——比无条件按 id 删除安全，也不会让升级卡住。

## 吞吐边界（写在这里，免得以后被当成 bug）

空转时每个 worker 每 `QUEUE_POLL_INTERVAL_SEC` 查一次库（默认 0.2s）。
10 个 worker = 50 次/秒的空查询，sqlite / 小 PG 都能扛，但它**不是**高吞吐队列。
真要上万 QPS 再换 Redis 或 PG 的 `LISTEN/NOTIFY`——只需新增一个 `infra/queue/<name>.py`，
协议与业务代码都不用动（见 `api/docs/EXTENDING.md` 的「队列换成 Redis」）。

## 空转时退避（省掉大部分没人要的查询）

队列绝大多数时间是空的，而「空」这个状态不需要每 200ms 确认一次。所以连续空转时
间隔按 2 倍增长，封顶 `QUEUE_IDLE_MAX_INTERVAL_SEC`（默认 1.0s）；一旦领到任务立刻
回到 `QUEUE_POLL_INTERVAL_SEC`。

**代价是明确写出来的**：封顶值就是「空闲时入队到被取走」的最坏延迟。默认 1 秒，
比 200ms 差，但空转查询少了 5 倍——对「短 job + 延迟重投」这种模型（见上一节）来说，
1 秒抖动远小于一次供应商轮询的间隔。要低延迟就把封顶调回等于 `POLL`，退避即消失。

`queue_polls_total{outcome}` 让这件事可验证：改之前/之后各看一次空转速率，
而不是靠「感觉好像省了」。

## 进程内唤醒：退避不等于「睡着了叫不醒」

退避解决的是「没人要的时候别空查」，但上面的代价（最坏 1 秒）在**同进程部署**下
是不必要的——API 和 worker 在同一个 event loop 里，`enqueue` 完全可以直接把等待
中的 worker 拍醒。

所以 `enqueue(delay=0)` 成功后置位本进程的 `asyncio.Event`，`dequeue` 用
`wait_for(event.wait(), timeout=退避间隔)` 代替纯 `sleep`：有人入队就立刻重试并复位
退避，没人入队就睡满退避间隔再查。

**它没有解决跨进程**：另一个实例的 event loop 不在这个进程里，`Event.set()` 够不着。
多实例（API 与 worker 分进程）的最坏延迟仍然由 `QUEUE_IDLE_MAX_INTERVAL_SEC` 决定。
真要 0 延迟只能是 PG 的 `LISTEN/NOTIFY` 或 Redis——那会引入「两套取任务路径」，
不做（见 T26）。

`delay > 0` 的入队**不唤醒**：那些任务在 `run_at` 之前根本不可见，叫醒只会让 worker
白查一次，而「短 job + 延迟重投」恰恰是延迟入队的大户。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4
from weakref import WeakKeyDictionary

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import trace
from core.config import settings
from core.logging import get_logger
from core.metrics import METRICS
from db.base import utcnow
from db.jobs import ACTIVE_JOB_STATUSES, JOB_DEAD, JOB_PENDING, JOB_RUNNING, QueuedJob
from db.session import get_session_factory
from infra.queue.base import Job, current_job

logger = get_logger(__name__)

# 每次 `_claim()` 一次：区分「查到了」和「白查」。空转速率是退避有没有生效的唯一
# 实测依据（改配置前记一次，改完再记一次）。
POLLS = METRICS.counter(
    "queue_polls_total",
    "取任务轮询次数",
    ("outcome",),
)


# 每个 event loop 一个唤醒开关。按 loop 隔离而不是全局一个：`asyncio.Event` 绑定
# 创建它的 loop，而测试里每个 `asyncio.run()` 都会新建 loop，复用跨 loop 的 Event
# 会直接抛「bound to a different loop」（和 `infra/cache/memory.py` 不用
# `asyncio.Lock` 同一个理由）。WeakKeyDictionary 让 loop 消失后自己不成为泄漏。
_WAKERS: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Event] = WeakKeyDictionary()


def _waker() -> asyncio.Event:
    loop = asyncio.get_running_loop()
    event = _WAKERS.get(loop)
    if event is None:
        event = asyncio.Event()
        _WAKERS[loop] = event
    return event


def wake_workers() -> None:
    """叫醒**本进程**正在等任务的 worker。

    跨进程够不着（见模块文档）：多实例部署仍有退避上限的延迟。
    `call_soon_threadsafe` 而不是直接 `event.set()`：入队可能发生在别的线程
    （线程池里的 handler、同步 CLI 脚本），直接 set 不是线程安全的。
    """
    for loop, event in list(_WAKERS.items()):
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            # loop 已关闭（测试收尾、进程退出）：忽略，不会有人再等它
            continue


async def _woken_within(event: asyncio.Event, *, budget: float) -> bool:
    """最多等 `budget` 秒，返回「是不是被入队叫醒的」。"""
    try:
        async with asyncio.timeout(budget):
            await event.wait()
    except TimeoutError:
        return False
    return True


def _decode(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        # 载荷坏了不能把队列卡死：交给 handler 自己判断「空参数」的后果，
        # 至少 job 能被消费掉并留痕
        logger.warning("队列载荷不是合法 JSON，按空载荷处理")
        return {}
    return value if isinstance(value, dict) else {"value": value}


class DbJobQueue:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        lease_sec: float | None = None,
        poll_interval_sec: float | None = None,
        idle_max_interval_sec: float | None = None,
    ) -> None:
        # 不在这里拿 session：`get_queue()` 可能在数据库初始化之前就被调用
        self._factory = session_factory
        self._lease = max(1.0, settings.queue_lease_sec if lease_sec is None else lease_sec)
        poll = settings.queue_poll_interval_sec if poll_interval_sec is None else poll_interval_sec
        self._poll = max(0.01, poll)
        idle_max = (
            settings.queue_idle_max_interval_sec
            if idle_max_interval_sec is None
            else idle_max_interval_sec
        )
        # 封顶不能小于基础间隔，否则「退避」反而变成了更密的轮询
        self._idle_max = max(self._poll, idle_max)
        # 连续空转次数：只在这个驱动的进程内累积，命中一次即清零
        self._idle = 0

    def _session(self) -> AsyncSession:
        factory = self._factory or get_session_factory()
        return factory()

    @property
    def lease_sec(self) -> float | None:
        """worker 按它的 1/4 续租（见 `workers.py`）。与 `_lease` 同源，不另存一份。"""
        return self._lease

    # ------------------------------------------------------------------ 入队
    async def enqueue(self, job: Job, *, delay: float = 0) -> str:
        # 入队即快照 trace：异步段的日志要能接回触发它的那个请求（见 core/trace.py）
        if not job.trace_id:
            job.trace_id = trace.current()
        run_at = utcnow() + timedelta(seconds=max(0.0, delay))
        parent = current_job()
        if parent and job.idempotency_key and parent.idempotency_key == job.idempotency_key:
            # Never fall through to normal enqueue on lost ownership: a stale
            # handler must not create a replacement after another worker finished.
            continuation = json.dumps(
                {
                    "name": job.name,
                    "payload": job.payload,
                    "attempts": job.attempts,
                    "max_attempts": job.max_attempts,
                    "timeout_sec": job.timeout_sec,
                    "trace_id": job.trace_id,
                    "run_at": run_at.isoformat(),
                },
                ensure_ascii=False,
                default=str,
            )
            async with self._session() as session:
                await session.execute(
                    update(QueuedJob)
                    .where(
                        QueuedJob.id == parent.id,
                        QueuedJob.status == JOB_RUNNING,
                        QueuedJob.lease_token == parent.lease_token,
                        QueuedJob.next_job.is_(None),
                    )
                    .values(next_job=continuation)
                )
                await session.commit()
            return parent.id
        async with self._session() as session:
            if job.idempotency_key:
                existing = await session.scalar(
                    select(QueuedJob.id).where(
                        QueuedJob.idempotency_key == job.idempotency_key,
                        QueuedJob.status.in_(ACTIVE_JOB_STATUSES),
                    )
                )
                if existing:
                    return existing

            session.add(
                QueuedJob(
                    id=job.id,
                    name=job.name,
                    payload=json.dumps(job.payload, ensure_ascii=False, default=str),
                    status=JOB_PENDING,
                    run_at=run_at,
                    attempts=job.attempts,
                    max_attempts=job.max_attempts,
                    idempotency_key=job.idempotency_key,
                    timeout_sec=job.timeout_sec,
                    trace_id=job.trace_id,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                # 并发入队撞上部分唯一索引：说明已经在途，返回先到的那个 id
                await session.rollback()
                existing = await session.scalar(
                    select(QueuedJob.id).where(
                        QueuedJob.idempotency_key == job.idempotency_key,
                        QueuedJob.status.in_(ACTIVE_JOB_STATUSES),
                    )
                )
                if existing:
                    return str(existing)
                raise
        # 立刻叫醒本进程的 worker。`delay > 0` 时不叫：那些行在 `run_at` 之前
        # 根本不可见，叫醒只会白查一次，而延迟入队正是「短 job + 延迟重投」的大户。
        if delay <= 0:
            wake_workers()
        return job.id

    # ------------------------------------------------------------------ 出队
    async def dequeue(self, timeout: float = 1.0) -> Job | None:  # noqa: ASYNC109 —— 协议规定的是单次等待上限
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        wake = _waker()
        while True:
            # 先清后查：清之前发生的入队一定被这次 `_claim` 看到；查之后发生的入队
            # 一定能把 event 置位。反过来（先查后清）会丢掉「查完到清之间」的入队，
            # 那一次唤醒就白丢了——症状是偶发的「入队后干等满一个退避间隔」。
            wake.clear()
            job = await self._claim()
            if job is not None:
                self._idle = 0
                return job
            self._idle = min(self._idle + 1, 16)
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            delay = self._next_interval()
            if await _woken_within(wake, budget=min(delay, remaining)):
                # 有任务刚入队：立刻重查，并且把退避复位（队列从「空」变成「非空」）
                self._idle = 0
                continue
            if delay >= remaining:
                # 这一觉睡到了超时：醒来就该返回，不必再查一次库。
                # 每个周期末尾的那次查询是纯浪费——醒来时已经过了 deadline，
                # 查完必然又是 miss。入队延迟不受影响：worker 的下一次 `dequeue`
                # 第一个动作就是查库，间隔在微秒级。
                return None

    def _next_interval(self) -> float:
        """下一次空转等待多久：`poll` 起，每次翻倍，封顶 `idle_max`。

        指数而不是线性：队列的空闲是**成段时间**的（夜里、发版间隙），翻倍能在
        两三次查询内到达封顶；线性增长会让「半忙」的时段一直停在小间隔上。
        """
        return min(self._poll * (2.0 ** min(self._idle, 16)), self._idle_max)

    async def _claim(self) -> Job | None:
        now = utcnow()
        pending_ready = and_(QueuedJob.status == JOB_PENDING, QueuedJob.run_at <= now)
        lease_expired = and_(
            QueuedJob.status == JOB_RUNNING,
            QueuedJob.lease_expires_at.is_not(None),
            QueuedJob.lease_expires_at < now,
        )
        async with self._session() as session:
            candidate = await session.scalar(
                select(QueuedJob.id)
                .where(or_(pending_ready, lease_expired))
                .order_by(QueuedJob.run_at)
                .limit(1)
            )
            if candidate is None:
                POLLS.inc(outcome="miss")
                return None
            POLLS.inc(outcome="hit")

            row = await session.get(QueuedJob, candidate)
            if row is None:
                return None

            expired = row.status == JOB_RUNNING
            attempts = row.attempts + 1 if expired else row.attempts
            if expired and attempts >= row.max_attempts:
                await session.execute(
                    update(QueuedJob)
                    .where(QueuedJob.id == candidate, lease_expired)
                    .execution_options(synchronize_session=False)
                    .values(
                        status=JOB_DEAD,
                        attempts=attempts,
                        lease_expires_at=None,
                        lease_token=None,
                        idempotency_key=None,
                        last_error="租约连续过期：执行者反复崩溃或停机",
                        updated_at=now,
                    )
                )
                await session.commit()
                logger.error(
                    "任务连续 %d 次租约过期，转死信 id=%s name=%s", attempts, row.id, row.name
                )
                return None

            # 条件更新才是领取：抢输的 worker rowcount=0，回头再找下一行。
            # 凭据在同一句里换掉：从这一刻起，旧主人的回执和续租都会落空。
            token = uuid4().hex
            claimed = await session.execute(
                update(QueuedJob)
                .where(
                    QueuedJob.id == candidate,
                    or_(pending_ready, lease_expired),
                    # The payload below was read from this exact ownership state.
                    # An ack may have promoted a different payload under this id
                    # between our read and update; retry rather than return it stale.
                    QueuedJob.status == row.status,
                    QueuedJob.lease_token == row.lease_token,
                )
                .execution_options(synchronize_session=False)
                .values(
                    status=JOB_RUNNING,
                    lease_expires_at=now + timedelta(seconds=self._lease),
                    lease_token=token,
                    next_job=None,
                    attempts=attempts,
                    updated_at=now,
                )
            )
            if getattr(claimed, "rowcount", 0) != 1:
                await session.rollback()
                return None
            await session.commit()

            return Job(
                id=row.id,
                name=row.name,
                payload=_decode(row.payload),
                attempts=attempts,
                max_attempts=row.max_attempts,
                idempotency_key=row.idempotency_key,
                timeout_sec=row.timeout_sec,
                trace_id=row.trace_id or "",
                lease_token=token,
                created_at=row.created_at,
            )

    # ------------------------------------------------------------------ 续租
    async def renew(self, job: Job) -> bool:
        """把租约往后推；返回 False = **这行已经不归你了**。

        `job.lease_token` 为空（手工构造的 Job、升级前就在跑的行）时返回 True：
        没有凭据就没有「持有」这个概念，worker 继续跑即可——真正的保护在
        `ack`/`nack` 那一侧（它们退化成只动 `running` 行）。
        """
        if not job.lease_token:
            return True
        now = utcnow()
        async with self._session() as session:
            result = await session.execute(
                update(QueuedJob)
                .where(
                    QueuedJob.id == job.id,
                    QueuedJob.lease_token == job.lease_token,
                    QueuedJob.status == JOB_RUNNING,
                )
                .execution_options(synchronize_session=False)
                .values(
                    lease_expires_at=now + timedelta(seconds=self._lease),
                    updated_at=now,
                )
            )
            await session.commit()
            return getattr(result, "rowcount", 0) == 1

    # ------------------------------------------------------------------ 确认
    def _fence(self, job: Job) -> list[Any]:
        """回执的作用域：必须是「我领走的那一行」，不能只看 id。

        没带凭据时退一步只认 `running` 行：比原来按 id 无条件删除安全
        （不会误删排队中的任务），也不至于让升级前入队的 job 卡死。
        """
        clauses: list[Any] = [QueuedJob.id == job.id]
        if job.lease_token:
            clauses.append(QueuedJob.lease_token == job.lease_token)
        else:
            clauses.append(QueuedJob.status == JOB_RUNNING)
        return clauses

    async def ack(self, job: Job) -> None:
        async with self._session() as session:
            # Lock on PG; the conditional update/delete below also fences SQLite.
            row = await session.scalar(select(QueuedJob).where(*self._fence(job)).with_for_update())
            if row is not None and row.next_job:
                staged = row.next_job
                values = json.loads(staged)
                values["payload"] = json.dumps(values["payload"], ensure_ascii=False, default=str)
                values["run_at"] = datetime.fromisoformat(values["run_at"])
                values.update(
                    status=JOB_PENDING,
                    lease_token=None,
                    lease_expires_at=None,
                    next_job=None,
                    updated_at=utcnow(),
                )
                result = await session.execute(
                    update(QueuedJob)
                    .where(
                        *self._fence(job),
                        QueuedJob.next_job == staged,
                    )
                    .values(**values)
                )
            else:
                result = await session.execute(
                    delete(QueuedJob).where(
                        *self._fence(job),
                        QueuedJob.next_job.is_(None),
                    )
                )
            await session.commit()
            if getattr(result, "rowcount", 0) != 1:
                # 不是错误：租约被接手后旧主人的回执本来就该落空。
                # 但它是「有任务跑了两遍」的**唯一痕迹**，所以要留一行日志。
                logger.warning(
                    "回执被忽略：这条 job 已不归本 worker（可能已被接手）id=%s name=%s",
                    job.id,
                    job.name,
                )

    async def nack(self, job: Job, *, requeue: bool = True) -> None:
        job.with_attempt()
        now = utcnow()
        # 死信保留行（排查「为什么这条没跑」比省空间重要），但清掉幂等键，
        # 否则同一个 key 想重投会被一条永远不动的死信挡回去
        dead = not requeue or job.exhausted
        values: dict[str, Any] = {
            "next_job": None,
            "attempts": job.attempts,
            "lease_expires_at": None,
            # 交回凭据：回到 pending 的行不该再记着「它属于谁」
            "lease_token": None,
            "updated_at": now,
        }
        if dead:
            values["status"] = JOB_DEAD
            values["idempotency_key"] = None
            values["last_error"] = "重试次数用尽" if requeue else "调用方放弃重试"
        else:
            values["status"] = JOB_PENDING
            values["run_at"] = now + timedelta(seconds=job.retry_delay())

        async with self._session() as session:
            result = await session.execute(
                update(QueuedJob).where(*self._fence(job)).values(**values)
            )
            await session.commit()
            if getattr(result, "rowcount", 0) != 1:
                logger.warning(
                    "重投被忽略：这条 job 已不归本 worker（可能已被接手）id=%s name=%s",
                    job.id,
                    job.name,
                )

    async def size(self) -> int:
        async with self._session() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(QueuedJob)
                    .where(QueuedJob.status.in_(ACTIVE_JOB_STATUSES))
                )
                or 0
            )

    async def close(self) -> None:
        # 连接池由 `db.session.dispose_engine()` 统一释放，这里没有需要关的东西。
        # 也**不要**在这里删表或清空：队列的持久化正是这个驱动的意义。
        return None

    # ------------------------------------------------------------------ 运维
    async def stats(self) -> dict[str, int]:
        """各状态计数，供 `app doctor` 与排查用。不在协议里（别的驱动没有这个概念）。"""
        async with self._session() as session:
            rows = await session.execute(
                select(QueuedJob.status, func.count()).group_by(QueuedJob.status)
            )
            counts = {status: int(count) for status, count in rows.all()}
        for status in (JOB_PENDING, JOB_RUNNING, JOB_DEAD):
            counts.setdefault(status, 0)
        return counts


Driver = DbJobQueue

__all__ = ["DbJobQueue", "Driver"]
