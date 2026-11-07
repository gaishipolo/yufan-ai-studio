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

## 为什么没有 ack 之外的「进度上报」

协议里只有 6 个方法（见 `infra/queue/base.py`）。长任务（视频生成几十分钟）靠的是
**短 job + 延迟重投**：`enqueue(job, delay=8)` 做轮询，而不是一个 job 跑 20 分钟。
所以租约默认 300 秒足够覆盖单次 handler，不需要心跳续租这一层复杂度。

## 吞吐边界（写在这里，免得以后被当成 bug）

空转时每个 worker 每 `QUEUE_POLL_INTERVAL_SEC` 查一次库（默认 0.2s）。
10 个 worker = 50 次/秒的空查询，sqlite / 小 PG 都能扛，但它**不是**高吞吐队列。
真要上万 QPS 再换 Redis 或 PG 的 `LISTEN/NOTIFY`——只需新增一个 `infra/queue/<name>.py`，
协议与业务代码都不用动（见 `api/docs/EXTENDING.md` 的「队列换成 Redis」）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import trace
from core.config import settings
from core.logging import get_logger
from db.base import utcnow
from db.jobs import ACTIVE_JOB_STATUSES, JOB_DEAD, JOB_PENDING, JOB_RUNNING, QueuedJob
from db.session import get_session_factory
from infra.queue.base import Job

logger = get_logger(__name__)


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
    ) -> None:
        # 不在这里拿 session：`get_queue()` 可能在数据库初始化之前就被调用
        self._factory = session_factory
        self._lease = max(1.0, settings.queue_lease_sec if lease_sec is None else lease_sec)
        poll = settings.queue_poll_interval_sec if poll_interval_sec is None else poll_interval_sec
        self._poll = max(0.01, poll)

    def _session(self) -> AsyncSession:
        factory = self._factory or get_session_factory()
        return factory()

    # ------------------------------------------------------------------ 入队
    async def enqueue(self, job: Job, *, delay: float = 0) -> str:
        # 入队即快照 trace：异步段的日志要能接回触发它的那个请求（见 core/trace.py）
        if not job.trace_id:
            job.trace_id = trace.current()
        run_at = utcnow() + timedelta(seconds=max(0.0, delay))
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
        return job.id

    # ------------------------------------------------------------------ 出队
    async def dequeue(self, timeout: float = 1.0) -> Job | None:  # noqa: ASYNC109 —— 协议规定的是单次等待上限
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        while True:
            job = await self._claim()
            if job is not None:
                return job
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(self._poll, remaining))

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
                return None

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

            # 条件更新才是领取：抢输的 worker rowcount=0，回头再找下一行
            claimed = await session.execute(
                update(QueuedJob)
                .where(QueuedJob.id == candidate, or_(pending_ready, lease_expired))
                .execution_options(synchronize_session=False)
                .values(
                    status=JOB_RUNNING,
                    lease_expires_at=now + timedelta(seconds=self._lease),
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
                created_at=row.created_at,
            )

    # ------------------------------------------------------------------ 确认
    async def ack(self, job: Job) -> None:
        async with self._session() as session:
            await session.execute(delete(QueuedJob).where(QueuedJob.id == job.id))
            await session.commit()

    async def nack(self, job: Job, *, requeue: bool = True) -> None:
        job.with_attempt()
        now = utcnow()
        # 死信保留行（排查「为什么这条没跑」比省空间重要），但清掉幂等键，
        # 否则同一个 key 想重投会被一条永远不动的死信挡回去
        dead = not requeue or job.exhausted
        values: dict[str, Any] = {
            "attempts": job.attempts,
            "lease_expires_at": None,
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
            await session.execute(
                update(QueuedJob).where(QueuedJob.id == job.id).values(**values)
            )
            await session.commit()

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
