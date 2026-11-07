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

# 取任务失败后的退避：0.5s 起，每次翻倍，封顶 30s。
# 为什么不能「失败就立刻重试」：数据库抖动时每个 worker 每秒打几十次连接，
# 连接池耗尽后连 `/health/ready` 都查不动，表现成「服务整个挂了」。
DEQUEUE_BACKOFF_BASE = 0.5
DEQUEUE_BACKOFF_MAX = 30.0

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
# 取任务失败（队列驱动异常）与「任务执行失败」是两种故障：
# 前者意味着**这台 worker 在空转**，后者意味着某个任务有问题。分开计数才能分清。
QUEUE_ERRORS = METRICS.counter(
    "queue_dequeue_errors_total",
    "worker 取任务失败次数（队列驱动异常）",
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
            task = asyncio.create_task(self._loop(i), name=f"worker-{i}")
            # 最后一道记录：正常情况下 `_loop` 只在停机时退出，走到这里说明有东西
            # 逃过了那三层兜底。至少让它出现在日志里，而不是「后台任务悄悄停了」。
            task.add_done_callback(self._on_task_done)
            self._tasks.append(task)
        logger.info(
            "worker 已启动：并发 %d，handler %s", self._concurrency, handler_names()
        )

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled() or self._stopping.is_set():
            return
        exc = task.exception()
        logger.error("worker 协程 %s 意外退出：%r", task.get_name(), exc)

    async def _loop(self, index: int) -> None:
        """单个消费协程。**这个函数不允许因为异常退出**（`CancelledError` 除外）。

        为什么值得这么小心：队列驱动（db / redis）在数据库抖动、连接池耗尽、
        网络闪断时都会抛异常。异常一旦冒出去，`create_task` 建的这个协程就结束了，
        而除了 `stop()` 之外没有任何东西会重启它——结果是
        「进程活着、`/health` 探针绿、后台任务全停」，并且上游恢复后也不会自愈，
        只能靠重启进程。这类故障不会出现在任何告警里。

        所以分三层兜底：取任务失败 → 退避重试；任务执行/回执失败 → 记指标继续；
        其余意外 → 记日志继续。只有停机信号能让它退出。
        """
        failures = 0
        while not self._stopping.is_set():
            try:
                job = await self._queue.dequeue(timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 见方法注释：这里不能抛
                failures += 1
                QUEUE_ERRORS.inc()
                # 指数退避：队列不可用时不要空转，把连接池和日志一起打满
                delay = min(
                    DEQUEUE_BACKOFF_BASE * 2 ** min(failures - 1, 5), DEQUEUE_BACKOFF_MAX
                )
                # 只在头几次和每 10 次记一条：持续故障时每轮一行日志等于自己刷屏
                if failures <= 3 or failures % 10 == 0:
                    logger.warning(
                        "worker %d 取任务失败（连续 %d 次，%.1fs 后重试）：%s: %s",
                        index,
                        failures,
                        delay,
                        type(exc).__name__,
                        exc,
                    )
                await asyncio.sleep(delay)
                continue

            if failures:
                logger.info("worker %d 取任务已恢复（此前连续失败 %d 次）", index, failures)
                failures = 0

            if job is None:
                # 让出一次控制权，防驱动「不 await 直接返回 None」时变成忙等。
                # 真实驱动（条件变量 / 查库）都会 await，但这是成本可忽略的兜底：
                # 忙等的后果是 CPU 打满 + 协程无法被 cancel，比多一次调度难查得多。
                await asyncio.sleep(0)
                continue

            try:
                await self._handle(index, job)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 兜底：回执本身失败也不能让协程死
                logger.exception("worker %d 处理任务时出现意外异常 job=%s", index, job.id)

    async def _handle(self, index: int, job: Job) -> None:
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
                return

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
                    "任务执行失败 name=%s id=%s attempt=%d worker=%d",
                    job.name,
                    job.id,
                    job.attempts + 1,
                    index,
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
