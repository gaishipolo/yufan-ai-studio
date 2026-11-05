"""生成编排执行器：把「提交 / 轮询 / 重试 / 超时 / 事件」标准化。

一次生成任务的完整生命周期：

    dispatch(kind, id)
        └─ enqueue(JOB_SUBMIT)
              ├─ 幂等：已有 external_id 则跳过提交
              ├─ provider.submit() → mark_submitted()
              └─ enqueue(JOB_POLL, delay=2s)
                    ├─ provider.poll() → mark_progress()
                    ├─ 未完成 → enqueue(JOB_POLL, delay=2s)   ← 关键：不在 handler 里 sleep
                    ├─ 成功 → mark_succeeded() + generation.succeeded 事件
                    └─ 失败 → mark_failed() + generation.failed 事件

为什么必须是「延迟重投」而不是「handler 内 sleep」：
worker 的并发槽位是稀缺资源。单个视频生成要 3 分钟，如果 handler 里 sleep，
2 个并发就只能同时跑 2 个任务；延迟重投下，槽位只被「真正在调 API 的那几十毫秒」
占用，同样的并发可以支撑成百上千个在途任务。

业务模块完全不需要接触本文件：实现 `GenerationPipeline` 并注册即可。
"""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.events import get_event_bus
from core.exceptions import AppError
from core.logging import get_logger
from db.session import get_session_factory
from generation.events import (
    EVT_GENERATION_FAILED,
    EVT_GENERATION_PROGRESS,
    EVT_GENERATION_SUBMITTED,
    EVT_GENERATION_SUCCEEDED,
)
from generation.pipeline import get_pipeline
from generation.store import GenerationStore
from infra import get_model_router
from infra.ai.base import GenerationState
from infra.queue.base import Job, JobQueue

logger = get_logger(__name__)

JOB_SUBMIT = "generation.submit"
JOB_POLL = "generation.poll"

_MAX_ERROR_LEN = 500
_MAX_BACKOFF_SEC = 30.0

SUBMIT_UNKNOWN_MESSAGE = (
    "提交结果未知：请求已发出但未确认供应商是否受理，为避免重复计费未自动重试，"
    "请核对后手动重试"
)


def _possibly_delivered(exc: BaseException) -> bool:
    """这次提交失败有没有可能「供应商已经受理」。

    按 **HTTP 语义**分类，而不是按异常类（R4-01 的修正）：

    - 连接都没建立（DNS 解析 / 握手 / 被拒绝）：请求必然没送达 → False。
    - `AppError` 4xx（含 429）：供应商明确拒绝，没受理 → False。
    - `AppError` 5xx / 408：**可能已受理**。网关 502 的典型成因就是
      「上游已经受理、回程失败」，把它当「确定没送达」等于用一次自动重提
      去赌对方没收到，赌错就是第二份账单。
    - `AppError` 501：功能没实现，请求根本没发出去 → False（否则脚手架
      生成的第一条任务会顶着「可能已计费」的文案失败）。
    - 读/写超时、连接中途被重置、其它未知异常：按「可能已送达」处理。
      宁可让一条任务失败等人核对，也不能赌它没送达。

    注：这里只识别 httpx 的异常类别；用别的 HTTP 客户端的供应商抛出的
    超时不会被精确归类，会落到「可能已送达」这个安全默认值。
    """
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return False
    if isinstance(exc, AppError):
        if exc.http_status == 501:
            return False
        return exc.http_status == 408 or exc.http_status >= 500
    return True


class GenerationRunner:
    def __init__(self, queue: JobQueue) -> None:
        self._queue = queue

    # ------------------------------------------------------------------ 入口
    async def dispatch(self, kind: str, record_id: str) -> str:
        """任务创建后调用：把提交动作入队。

        业务 service 只写这一行，不关心供应商、重试、轮询。
        """
        get_pipeline(kind)  # 未注册立即暴露，而不是异步失败后才发现
        job = Job(
            name=JOB_SUBMIT,
            payload={"kind": kind, "record_id": record_id},
            idempotency_key=f"submit:{kind}:{record_id}",
            timeout_sec=120.0,
        )
        return await self._queue.enqueue(job)

    async def resume(self, kind: str, record_id: str, *, delay: float = 0) -> None:
        """进程重启后恢复：只续轮询，**绝不重新提交**。

        已有 `external_id` 意味着供应商侧任务已经在跑（也可能已经扣费），
        再走一遍 `dispatch` 会重复提交、重复计费，并生成两个不同的产物。

        注意：轮询预算是「本次进程生命周期」内的计数，重启会归零。
        也就是说反复重启的极端情况下，单条任务可能超过 `generation_timeout_sec`
        仍在轮询。要严格按墙钟时间算，得把 polls 持久化到记录上（当前未做，
        因为重启本身是异常事件，且供应商侧同样有自己的超时）。
        """
        get_pipeline(kind)
        await self._schedule_poll(kind, record_id, polls=0, delay=delay)

    # ------------------------------------------------------------------ 提交
    async def handle_submit(self, job: Job) -> None:
        kind, record_id = self._ticket(job)
        pipeline = get_pipeline(kind)
        factory = get_session_factory()
        external_id: str | None = None

        async with factory() as session:
            store = pipeline.store(session)
            record = await store.load(record_id)
            if record is None:
                logger.warning("生成记录不存在，跳过 kind=%s id=%s", kind, record_id)
                return
            if record.terminal:
                return

            if record.submitted:
                # 幂等：上次提交其实成功了，只是没来得及写库
                logger.info("记录已有 external_id，跳过提交 id=%s", record_id)
                external_id = record.external_id
            elif record.submit_unknown:
                # 进程在「供应商已受理 → 写库」之间崩过。库里既没有 external_id
                # 也没有成功记录，无法判断供应商是否已经扣费，因此**绝不自动重提**：
                # 宁可让一条任务失败等人核对，也不能产生第二份账单。
                logger.warning(
                    "提交结果未知，跳过自动重提（可能已计费）kind=%s id=%s attempts=%d",
                    kind,
                    record_id,
                    record.submit_attempts,
                )
                await self._fail(
                    store=store,
                    session=session,
                    kind=kind,
                    record_id=record_id,
                    owner_id=record.owner_id,
                    message=SUBMIT_UNKNOWN_MESSAGE,
                )
                return
            else:
                # 「记录提交意图」不是「调用供应商」：它出错说明代码有问题
                # （典型是脚手架里的占位实现），此时请求根本没发出去。
                # 必须在 try 之外拦下来：落进下面的分支会被误判成「可能已计费」（R4-03）
                try:
                    await store.mark_submitting(record_id)
                    await session.commit()
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "提交准备失败（未调用供应商）kind=%s id=%s", kind, record_id
                    )
                    await self._fail(
                        store=store,
                        session=session,
                        kind=kind,
                        record_id=record_id,
                        owner_id=record.owner_id,
                        message=f"提交准备失败：{exc}"[:_MAX_ERROR_LEN],
                    )
                    return

                try:
                    provider = get_model_router().provider_for(record.model)
                    request = await pipeline.build_request(record)
                    external_id = await provider.submit(request)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("提交生成失败 kind=%s id=%s", kind, record_id)

                    if _possibly_delivered(exc):
                        # 超时 / 5xx / 连接中途断开：请求已发出，供应商可能已受理。
                        # 清标记重试就是在赌「它没收到」，赌错 = 第二份账单，
                        # 所以保留意图标记、置为「结果未知」失败等人核对。
                        await self._fail(
                            store=store,
                            session=session,
                            kind=kind,
                            record_id=record_id,
                            owner_id=record.owner_id,
                            message=SUBMIT_UNKNOWN_MESSAGE,
                        )
                        return

                    # 确定没送达（4xx 拒绝 / 根本连不上 / 功能未实现）：
                    # 清掉意图标记，允许安全重试
                    await store.clear_submit_attempt(record_id)
                    if self._retryable(exc, job):
                        await session.commit()
                        raise  # 交给 worker nack + 指数退避重试

                    await self._fail(
                        store=store,
                        session=session,
                        kind=kind,
                        record_id=record_id,
                        owner_id=record.owner_id,
                        message=f"提交生成失败：{exc}"[:_MAX_ERROR_LEN],
                    )
                    return

                await store.mark_submitted(record_id, external_id)
                await session.commit()
                await self._emit(
                    EVT_GENERATION_SUBMITTED,
                    kind=kind,
                    record_id=record_id,
                    owner_id=record.owner_id,
                    model=record.model,
                    external_id=external_id,
                )

        await self._schedule_poll(
            kind, record_id, polls=0, delay=settings.generation_poll_interval_sec
        )

    # ------------------------------------------------------------------ 轮询
    async def handle_poll(self, job: Job) -> None:
        kind, record_id = self._ticket(job)
        polls = int(job.payload.get("polls", 0))
        pipeline = get_pipeline(kind)
        factory = get_session_factory()
        next_delay: float | None = None

        try:
            async with factory() as session:
                store = pipeline.store(session)
                record = await store.load(record_id)
                if record is None or record.terminal:
                    return
                if not record.external_id:
                    # 状态被重置过（如手动重试），重新走提交
                    await self.dispatch(kind, record_id)
                    return

                if polls >= settings.generation_max_polls:
                    # 事件里的 error 与落库文案必须一致：接上通知渠道后，
                    # 用户收到的说法不能和任务详情页不一样（R4-10）
                    message = "生成超时，请重试"
                    await store.mark_failed(record_id, message)
                    await session.commit()
                    await self._emit(
                        EVT_GENERATION_FAILED,
                        kind=kind,
                        record_id=record_id,
                        owner_id=record.owner_id,
                        error=message,
                    )
                    return

                provider = get_model_router().provider_for(record.model)
                try:
                    result = await provider.poll(record.external_id, model=record.model)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "轮询失败（稍后重试）kind=%s id=%s polls=%d err=%s",
                        kind,
                        record_id,
                        polls,
                        exc,
                    )
                    next_delay = self._backoff(polls)
                else:
                    state = result.state

                    if state == GenerationState.SUCCEEDED:
                        await store.mark_succeeded(
                            record_id,
                            output_url=result.output_url,
                            cover_url=result.cover_url,
                            progress=result.progress or 100,
                        )
                        await session.commit()
                        await self._emit(
                            EVT_GENERATION_SUCCEEDED,
                            kind=kind,
                            record_id=record_id,
                            owner_id=record.owner_id,
                            model=record.model,
                            output_url=result.output_url,
                            cover_url=result.cover_url,
                        )
                        return

                    if state == GenerationState.FAILED:
                        if result.retryable and polls + 1 < settings.generation_max_polls:
                            next_delay = self._backoff(polls)
                        else:
                            await store.mark_failed(
                                record_id, (result.error or "生成失败")[:_MAX_ERROR_LEN]
                            )
                            await session.commit()
                            await self._emit(
                                EVT_GENERATION_FAILED,
                                kind=kind,
                                record_id=record_id,
                                owner_id=record.owner_id,
                                error=result.error,
                            )
                            return
                    else:
                        if result.progress != record.progress:
                            await store.mark_progress(record_id, result.progress)
                            await session.commit()
                            await self._emit(
                                EVT_GENERATION_PROGRESS,
                                kind=kind,
                                record_id=record_id,
                                owner_id=record.owner_id,
                                progress=result.progress,
                            )
                        next_delay = settings.generation_poll_interval_sec
        except Exception:  # noqa: BLE001
            # 轮询 job 的 max_attempts=1，异常冒到 worker 会被直接丢弃；一旦丢弃，
            # 任务就永久停在 running（队列里已经没它的 job 了）。
            # 所以这里自己兜住：DB 抖动、进度写失败都只算「这一跳失败」，继续下一跳。
            logger.exception(
                "轮询执行异常（继续下一轮）kind=%s id=%s polls=%d", kind, record_id, polls
            )
            if next_delay is None:
                next_delay = self._backoff(polls)

        if next_delay is not None:
            await self._schedule_poll(kind, record_id, polls=polls + 1, delay=next_delay)

    # ------------------------------------------------------------------ 内部
    async def _schedule_poll(self, kind: str, record_id: str, *, polls: int, delay: float) -> None:
        job = Job(
            name=JOB_POLL,
            payload={"kind": kind, "record_id": record_id, "polls": polls},
            # 幂等：同一记录在队列中最多一个待执行的轮询
            idempotency_key=f"poll:{kind}:{record_id}",
            max_attempts=1,
            timeout_sec=60.0,
        )
        try:
            await self._queue.enqueue(job, delay=delay)
        except Exception:  # noqa: BLE001
            # 入队失败 = 链路断在这里，任务没有任何后续跳。先重试一次，
            # 仍失败就把任务显式置为失败，绝不让它静默停在 running。
            logger.exception("轮询入队失败，重试一次 kind=%s id=%s", kind, record_id)
            try:
                await self._queue.enqueue(job, delay=0)
            except Exception:  # noqa: BLE001
                logger.exception("轮询入队再次失败，标记任务失败 kind=%s id=%s", kind, record_id)
                await self._fail_orphan(kind, record_id, "轮询调度失败，请重试")

    async def _fail_orphan(self, kind: str, record_id: str, message: str) -> None:
        """链路已断时的兜底：用一个新会话把记录标成失败（失败也吞掉，只记日志）。"""
        try:
            async with get_session_factory()() as session:
                store = get_pipeline(kind).store(session)
                record = await store.load(record_id)
                if record is None or record.terminal:
                    return
                await store.mark_failed(record_id, message)
                await session.commit()
                await self._emit(
                    EVT_GENERATION_FAILED,
                    kind=kind,
                    record_id=record_id,
                    owner_id=record.owner_id,
                    error=message,
                )
        except Exception:  # noqa: BLE001
            logger.exception("兜底标记失败也失败（只能等重启恢复）kind=%s id=%s", kind, record_id)

    async def _fail(
        self,
        *,
        store: GenerationStore,
        session: AsyncSession,
        kind: str,
        record_id: str,
        owner_id: str,
        message: str,
    ) -> None:
        """统一的「确定失败」收尾：落库 → 提交 → 发事件。

        三个失败分支（准备失败 / 结果未知 / 确定未送达）文案不同但动作相同。
        各写一遍的后果已经有先例：`worker.py` 的「结果未知」分支漏发了事件（R4-10）。
        """
        await store.mark_failed(record_id, message)
        await session.commit()
        await self._emit(
            EVT_GENERATION_FAILED,
            kind=kind,
            record_id=record_id,
            owner_id=owner_id,
            error=message,
        )

    @staticmethod
    def _retryable(exc: Exception, job: Job) -> bool:
        """只有「可能自愈」的失败才占用重试次数。

        `BadRequest`（未知模型、参数非法）这类永久错误重试三次，结果是
        多等 2s+4s、并且在最后一次覆盖掉真正的错误信息。
        """
        if job.attempts + 1 >= job.max_attempts:
            return False
        if isinstance(exc, AppError):
            return exc.retryable
        return True

    @staticmethod
    def _backoff(polls: int) -> float:
        return min(
            _MAX_BACKOFF_SEC,
            settings.generation_poll_interval_sec * (2 ** min(polls, 4)),
        )

    @staticmethod
    def _ticket(job: Job) -> tuple[str, str]:
        kind = str(job.payload.get("kind", ""))
        record_id = str(job.payload.get("record_id", ""))
        if not kind or not record_id:
            raise ValueError(f"{job.name} 缺少 kind/record_id: {job.payload}")
        return kind, record_id

    @staticmethod
    async def _emit(name: str, **payload: object) -> None:
        await get_event_bus().publish(name, **payload)


def get_runner(queue: JobQueue | None = None) -> GenerationRunner:
    from infra import get_queue

    return GenerationRunner(queue or get_queue())


__all__ = ["JOB_POLL", "JOB_SUBMIT", "GenerationRunner", "get_runner"]
