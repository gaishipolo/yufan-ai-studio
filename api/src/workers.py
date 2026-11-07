"""后台 worker：消费队列、分发到已注册的 handler。

扩展方式：在自己的模块里写

    @register_handler("voice.clone")
    async def handle_clone(job: Job) -> None: ...

只要该模块被自动发现（`api/discovery.py` 会 import 它），handler 就自动生效，
**不需要修改本文件**。

本文件只负责与业务无关的三件事：并发消费、超时控制、失败重试。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from core import trace
from core.logging import get_logger
from core.metrics import JOB_BUCKETS, METRICS
from infra.queue.base import Job, JobQueue

logger = get_logger(__name__)

JobHandler = Callable[[Job], Awaitable[None]]

HANDLERS: dict[str, JobHandler] = {}

# 单次执行的默认超时，防止某个 handler 挂死拖垮并发槽位
DEFAULT_JOB_TIMEOUT_SEC = 300.0

# 任务指标：`outcome` 区分成功/异常/超时/取消/未知类型。
# 只看成功率不够——超时和未知类型是两种完全不同的故障，必须分开计数。
JOBS_PROCESSED = METRICS.counter(
    "jobs_processed_total",
    "任务执行结果数",
    ("name", "outcome"),
)
JOB_DURATION = METRICS.histogram(
    "job_duration_seconds",
    "任务执行耗时（秒）",
    ("name",),
    buckets=JOB_BUCKETS,
)


def register_handler(name: str, *, override: bool = False) -> Callable[[JobHandler], JobHandler]:
    def decorator(fn: JobHandler) -> JobHandler:
        if name in HANDLERS and not override:
            raise RuntimeError(
                f"job handler 重名: {name!r}（已注册 {HANDLERS[name].__module__}）"
            )
        HANDLERS[name] = fn
        return fn

    return decorator


def handler_names() -> list[str]:
    return sorted(HANDLERS)


class Worker:
    def __init__(self, queue: JobQueue, *, concurrency: int = 1) -> None:
        self._queue = queue
        self._concurrency = max(1, concurrency)
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if not HANDLERS:
            logger.info("未注册任何 job handler，worker 不启动")
            return
        for i in range(self._concurrency):
            self._tasks.append(asyncio.create_task(self._loop(i), name=f"worker-{i}"))
        logger.info(
            "worker 已启动：并发 %d，handler %s", self._concurrency, handler_names()
        )

    async def _loop(self, index: int) -> None:
        while not self._stopping.is_set():
            job = await self._queue.dequeue(timeout=1.0)
            if job is None:
                continue

            # 恢复入队时的 trace：此后这个任务的所有日志都带着「谁触发的那个请求」。
            # 执行结束必须还原，否则同一个 worker 的下一条任务会继承上一条的 id。
            previous = trace.adopt(job.trace_id)
            started = time.perf_counter()
            outcome = "error"
            label = job.name
            try:
                handler = HANDLERS.get(job.name)
                if handler is None:
                    label = "<unknown>"
                    outcome = "unknown"
                    logger.warning("未知任务类型 %s，丢弃", job.name)
                    await self._queue.ack(job)
                    continue

                try:
                    await asyncio.wait_for(
                        handler(job), timeout=job.timeout_sec or DEFAULT_JOB_TIMEOUT_SEC
                    )
                except TimeoutError:
                    outcome = "timeout"
                    logger.error(
                        "任务超时 name=%s id=%s attempt=%d", job.name, job.id, job.attempts + 1
                    )
                    await self._queue.nack(job)
                except asyncio.CancelledError:
                    # 停机时把任务放回队列，避免丢任务
                    outcome = "cancelled"
                    await self._queue.nack(job, requeue=True)
                    raise
                except Exception:
                    outcome = "error"
                    logger.exception(
                        "任务执行失败 name=%s id=%s attempt=%d", job.name, job.id, job.attempts + 1
                    )
                    await self._queue.nack(job)
                else:
                    outcome = "success"
                    await self._queue.ack(job)
            finally:
                JOBS_PROCESSED.inc(name=label, outcome=outcome)
                JOB_DURATION.observe(time.perf_counter() - started, name=label)
                trace.release(previous)

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("worker 已停止")
