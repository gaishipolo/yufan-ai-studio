"""后台 worker：消费队列、分发到已注册的 handler。

扩展方式：在自己的模块里写

    @register_handler("voice.clone")
    async def handle_clone(job: Job) -> None: ...

只要该模块被自动发现（`api/discovery.py` 会 import 它），handler 就自动生效，
**不需要修改本文件**。

本文件只负责与业务无关的四件事：并发消费、超时控制、失败重试、租约续租。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.orm.exc import StaleDataError

from core import trace
from core.logging import get_logger
from core.metrics import JOB_BUCKETS, METRICS
from infra.queue.base import Job, JobQueue, job_execution

logger = get_logger(__name__)

JobHandler = Callable[[Job], Awaitable[None]]

HANDLERS: dict[str, JobHandler] = {}

# 单次执行的默认超时，防止某个 handler 挂死拖垮并发槽位。
# 它可以比队列租约长——执行期间 worker 会续租（见 `_renew_lease`），
# 所以「handler 比租约久」不会变成「任务被执行两遍」。
DEFAULT_JOB_TIMEOUT_SEC = 300.0

# 并发写冲突（乐观锁落败）的重投次数与延迟。
# 这不是「失败重试」：`StaleDataError` 意味着这次读-改-写输给了另一个写者，
# 重跑一遍就能看到新状态继续推进。模板作业是 `max_attempts=1`（失败即死信，
# 重试靠新建作业），所以必须在作业层显式重投，否则「用户点删除时轮询正在写进度」
# 这种日常并发就会把任务扔进死信。
CONFLICT_RETRY_LIMIT = 3
CONFLICT_RETRY_DELAY_SEC = 2.0

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
# 协程存活情况（T31）。没有这两个数时，「worker 还在不在跑」只能靠
# `jobs_processed_total` 不涨反推——那要等积压出现才发现，且分不清两种完全不同的故障：
# 协程死了（state=alive < desired）与协程活着但卡在某个 handler 上（busy 长期等于并发数）。
WORKER_CONSUMERS = METRICS.gauge(
    "worker_consumers",
    "消费协程数：state=alive 实际存活，state=desired 配置应有多少",
    ("state",),
)
WORKER_BUSY = METRICS.gauge(
    "worker_jobs_in_flight",
    "正在执行的任务数；长期等于并发数说明有 handler 卡住了",
)
# 执行期间发现「这行已经不归我了」。它不是错误处理，而是**事故证据**：
# 出现即说明有 worker 卡住超过了一个完整租约，那个 handler 已经在别处又跑了一遍。
JOB_LEASE_LOST = METRICS.counter(
    "job_lease_lost_total",
    "任务在执行中被别人接手的次数",
    ("name",),
)


@dataclass
class _LeaseWatch:
    """一次执行的持有情况：续租任务写、收尾时读。"""

    work: asyncio.Task[None] | None = None
    lost: bool = False


def _being_cancelled() -> bool:
    """当前协程是不是「有人在 cancel 我」。

    用来区分两种 `CancelledError`：停机（要放回队列）与「租约没了、handler 被我们
    自己停掉」（要放手）。只看异常本身的类型是分不出来的。
    """
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


def register_handler(name: str, *, override: bool = False) -> Callable[[JobHandler], JobHandler]:
    def decorator(fn: JobHandler) -> JobHandler:
        if name in HANDLERS and not override:
            raise RuntimeError(f"job handler 重名: {name!r}（已注册 {HANDLERS[name].__module__}）")
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
            WORKER_CONSUMERS.set(0, state="desired")
            return
        WORKER_CONSUMERS.set(float(self._concurrency), state="desired")
        # 先落一个 0：gauge 只在有样本时才出现在 /metrics 里，空闲期「这条序列不存在」
        # 会让看板显示 no data、告警表达式也拿不到基线。
        WORKER_BUSY.set(0)
        for i in range(self._concurrency):
            task = asyncio.create_task(self._loop(i), name=f"worker-{i}")
            # 最后一道记录：正常情况下 `_loop` 只在停机时退出，走到这里说明有东西
            # 逃过了那三层兜底。至少让它出现在日志里，而不是「后台任务悄悄停了」。
            task.add_done_callback(self._on_task_done)
            self._tasks.append(task)
        logger.info("worker 已启动：并发 %d，handler %s", self._concurrency, handler_names())

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

        存活计数放在这一层而不是 `_consume` 里：`_consume` 的 `while` 有两个出口
        （停机、异常冒泡），数在循环里加减很容易在某个出口漏减，留下一个「看起来
        还有 2 个协程在跑」的假象——那比没有指标更糟。
        """
        WORKER_CONSUMERS.inc(state="alive")
        try:
            await self._consume(index)
        finally:
            WORKER_CONSUMERS.dec(state="alive")

    async def _consume(self, index: int) -> None:
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
                delay = min(DEQUEUE_BACKOFF_BASE * 2 ** min(failures - 1, 5), DEQUEUE_BACKOFF_MAX)
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
        WORKER_BUSY.inc()
        started = time.perf_counter()
        outcome = "error"
        label = job.name
        watch = _LeaseWatch()
        beat: asyncio.Task[None] | None = None
        try:
            handler = HANDLERS.get(job.name)
            if handler is None:
                label = "<unknown>"
                outcome = "unknown"
                logger.warning("未知任务类型 %s，丢弃", job.name)
                await self._queue.ack(job)
                return

            # handler 单独一个 task：租约丢了要能把它停下来。
            # 直接在 `_handle` 里 await 的话，「停手」就只能 cancel worker 自己的消费
            # 协程——那会少一个槽位，`_on_task_done` 还会记一条假故障。
            with job_execution(job):
                watch.work = asyncio.create_task(
                    asyncio.wait_for(
                        handler(job), timeout=job.timeout_sec or DEFAULT_JOB_TIMEOUT_SEC
                    )
                )
            if self._queue.lease_sec is not None:
                beat = asyncio.create_task(self._renew_lease(job, watch))
                # 谁先结束都回到这里：handler 结束是正常收尾，续租结束是租约丢了。
                await asyncio.wait({watch.work, beat}, return_when=asyncio.FIRST_COMPLETED)
            await watch.work
        except TimeoutError:
            outcome = "timeout"
            logger.error("任务超时 name=%s id=%s attempt=%d", job.name, job.id, job.attempts + 1)
            await self._queue.nack(job)
        except asyncio.CancelledError:
            if _being_cancelled():
                # 停机时把任务放回队列，避免丢任务。
                # 回执带凭据：若这行已被接手，它会被忽略而不是抢回来。
                outcome = "cancelled"
                await self._queue.nack(job, requeue=True)
                raise
            # 不是我们在被取消，而是 `_renew_lease` 停掉了 handler：这行已经归别人了。
            # **不回执**——ack 会删掉新主人正在跑的行使任务彻底消失，
            # nack 会把新主人的任务改成 pending 让他白跑。两者都是二次伤害。
            outcome = "lease_lost"
            logger.error(
                "租约已被接手，停止执行 name=%s id=%s（这个任务很可能已在别处重跑）",
                job.name,
                job.id,
            )
        except StaleDataError:
            # 乐观锁落败 = 另一个写者先提交了，不是故障，也不该进死信。
            # 这里重投一条新作业（模板作业 max_attempts=1，nack 会直接判死），
            # 用 payload 里的计数封顶，避免持续性冲突变成无限重投。
            if watch.lost:
                outcome = "lease_lost"
                logger.error(
                    "租约丢失后 handler 仍撞上并发写冲突 name=%s id=%s", job.name, job.id
                )
            else:
                retries = int(job.payload.get("conflict_retries") or 0)
                if retries >= CONFLICT_RETRY_LIMIT:
                    outcome = "conflict_giveup"
                    logger.error(
                        "并发写冲突重试 %d 次仍未成功，转入死信 name=%s id=%s",
                        retries,
                        job.name,
                        job.id,
                    )
                    await self._queue.nack(job)
                else:
                    outcome = "conflict"
                    logger.warning(
                        "并发写冲突，%0.1fs 后重投 name=%s id=%s（第 %d 次）",
                        CONFLICT_RETRY_DELAY_SEC,
                        job.name,
                        job.id,
                        retries + 1,
                    )
                    # 新作业不带幂等键：带上会命中「在途」去重、拿回自己这条，
                    # 等于没有重投。旧行随后 ack 掉；万一 ack 落败，多出来的一份
                    # 也会在读状态时撞上 CAS 安全退出（handler 每一步都是幂等的）。
                    try:
                        await self._queue.enqueue(
                            Job(
                                name=job.name,
                                payload={**job.payload, "conflict_retries": retries + 1},
                                max_attempts=job.max_attempts,
                                timeout_sec=job.timeout_sec,
                            ),
                            delay=CONFLICT_RETRY_DELAY_SEC,
                        )
                    except Exception:  # noqa: BLE001
                        # 队列本身不可用时退回死信：宁可留下一条可排查的记录，
                        # 也不要让异常从 `_handle` 逃出去打断消费循环。
                        outcome = "conflict_requeue_failed"
                        logger.exception(
                            "并发写冲突后重投失败，转入死信 name=%s id=%s", job.name, job.id
                        )
                        await self._queue.nack(job)
                    else:
                        await self._queue.ack(job)
        except Exception:
            if watch.lost:
                outcome = "lease_lost"
                logger.error("租约丢失后 handler 仍抛出异常 name=%s id=%s", job.name, job.id)
            else:
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
            if watch.lost:
                outcome = "lease_lost"
            else:
                outcome = "success"
                await self._queue.ack(job)
        finally:
            tasks = [task for task in (beat, watch.work) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                # gather(return_exceptions=True)：handler 的异常在这里已经分类过一遍，
                # 不能在收尾时再抛一次（那会盖掉真正的 outcome）
                await asyncio.gather(*tasks, return_exceptions=True)
            WORKER_BUSY.dec()
            JOBS_PROCESSED.inc(name=label, outcome=outcome)
            JOB_DURATION.observe(time.perf_counter() - started, name=label)
            trace.release(previous)

    async def _renew_lease(self, job: Job, watch: _LeaseWatch) -> None:
        """执行期间替 handler 续租；发现被接手就把 handler 停掉。

        节奏取租约的 1/4：留三次重试机会。续租本身抛异常（数据库抖一下）不算丢租约，
        下一轮接着试；真的丢了的时候 `renew` 会返回 False，那才是「这行不归你了」。

        没有这一层时，「租约到期 = 执行者崩了」这个推断只在「handler 一定比租约短」
        时成立，而那是约定不是机制：单次 handler 预算与默认租约都是 300 秒，
        一次慢调用滑过去，任务就会被第二个 worker 同时跑一遍。
        """
        lease = self._queue.lease_sec
        if lease is None:  # 调用方已判过；这里挡住第三方的错误用法
            return
        interval = max(0.05, lease / 4)
        while True:
            await asyncio.sleep(interval)
            try:
                alive = await self._queue.renew(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "续租失败，%.2fs 后重试 job=%s name=%s：%s: %s",
                    interval,
                    job.id,
                    job.name,
                    type(exc).__name__,
                    exc,
                )
                continue
            if alive:
                continue
            watch.lost = True
            JOB_LEASE_LOST.inc(name=job.name)
            logger.error(
                "续租失败：这条 job 已被别人接手（租约 %ss），停止执行 id=%s name=%s",
                lease,
                job.id,
                job.name,
            )
            if watch.work is not None:
                watch.work.cancel()
            return

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("worker 已停止")
