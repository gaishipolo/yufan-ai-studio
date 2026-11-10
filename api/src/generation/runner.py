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

import contextlib
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.events import get_event_bus
from core.exceptions import AppError
from core.logging import get_logger
from core.ports.accounts import account_is_active
from core.ports.library_ingest import get_library_ingest, has_library_ingest
from db.session import get_session_factory
from generation.events import (
    EVT_GENERATION_FAILED,
    EVT_GENERATION_PROGRESS,
    EVT_GENERATION_SUBMITTED,
    EVT_GENERATION_SUCCEEDED,
)
from generation.pipeline import get_pipeline
from generation.spec import GenerationRecord
from generation.store import GenerationStore
from infra import get_model_router
from infra.ai.base import GenerationProgress, GenerationState
from infra.artifacts import artifact_urls
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

OWNER_DISABLED_MESSAGE = "发起人账号已停用，任务未提交（避免继续产生费用）"


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
            timeout_sec=await self._submit_budget(kind, record_id),
        )
        return await self._queue.enqueue(job)

    #: 框架默认的提交预算（秒）。驱动没声明 `submit_timeout_sec` 时用它。
    _DEFAULT_SUBMIT_TIMEOUT_SEC = 120.0
    #: 在驱动自报预算之外留给建连 / 序列化 / 落库的余量
    _SUBMIT_TIMEOUT_MARGIN_SEC = 30.0

    async def _submit_budget(self, kind: str, record_id: str) -> float:
        """提交 job 的硬超时 = 驱动的提交预算 + 余量。

        写死一个值会与驱动声明打架：agnes 声明 180 秒（2K 图 / 上游排队），
        框架按 120 秒取消 handler，任务重投时 `submit_attempts=1` 且没有
        external_id，于是被判「提交结果未知」——钱可能花了、图丢了、还不许重试。
        预算解析失败一律回退默认值：算不出来也必须能提交。
        """
        declared: object = None
        try:
            async with get_session_factory()() as session:
                record = await get_pipeline(kind).store(session).load(record_id)
            if record is not None:
                resolver = getattr(get_pipeline(kind), "resolve_provider", None)
                provider = (
                    resolver(record.model)
                    if resolver is not None
                    else get_model_router().provider_for(record.model)
                )
                declared = getattr(provider, "submit_timeout_sec", None)
        except Exception:  # noqa: BLE001 - 预算算不出来不该阻止提交
            logger.warning("提交超时预算解析失败，按默认值 kind=%s id=%s", kind, record_id)
        if not isinstance(declared, (int, float)) or isinstance(declared, bool) or declared <= 0:
            return self._DEFAULT_SUBMIT_TIMEOUT_SEC
        return float(declared) + self._SUBMIT_TIMEOUT_MARGIN_SEC

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
                # 发起人已被停用：不再替一个被关在门外的人花钱。
                # 放在这里而不是更早，是因为「已经拿到 external_id」的任务在上面就返回了
                # （钱已经花了，停掉只会让成片烂在盘里），只有还没提交过的才拦。
                # 三态语义：None（查不到 / 没接实现）按放行，见 core/ports/accounts.py。
                if await account_is_active(record.owner_id) is False:
                    logger.warning(
                        "发起人已停用，取消提交 kind=%s id=%s owner=%s",
                        kind,
                        record_id,
                        record.owner_id,
                    )
                    await self._fail(
                        store=store,
                        session=session,
                        kind=kind,
                        record_id=record_id,
                        owner_id=record.owner_id,
                        message=OWNER_DISABLED_MESSAGE,
                    )
                    return

                # 「记录提交意图」不是「调用供应商」：它出错说明代码有问题
                # （典型是脚手架里的占位实现），此时请求根本没发出去。
                # 必须在 try 之外拦下来：落进下面的分支会被误判成「可能已计费」（R4-03）
                try:
                    if not await store.mark_submitting(record_id):
                        # 排队期间已被取消（或记录被删）：不能再向供应商提交，
                        # 否则用户已经看到「已取消」，账单却照样产生
                        logger.info("记录已终态，放弃提交 kind=%s id=%s", kind, record_id)
                        return
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

                # 「解析供应商 + 构造请求」都发生在请求发出**之前**，出错是我们
                # 自己的 bug（模型配置写错、记录缺字段）。放进下面的 try 会被
                # `_possibly_delivered` 判成「供应商可能已受理」——它对非 AppError
                # 一律返回 True——于是自动重试被永久放弃，锅推给了上游。
                try:
                    resolver = getattr(pipeline, "resolve_provider", None)
                    provider = (
                        resolver(record.model) if resolver is not None
                        else get_model_router().provider_for(record.model)
                    )
                    request = await pipeline.build_request(record)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "生成请求构造失败（未调用供应商）kind=%s id=%s", kind, record_id
                    )
                    await store.clear_submit_attempt(record_id)
                    await self._fail(
                        store=store,
                        session=session,
                        kind=kind,
                        record_id=record_id,
                        owner_id=record.owner_id,
                        message=f"生成请求无法构造：{exc}"[:_MAX_ERROR_LEN],
                    )
                    return

                try:
                    delivered = await provider.submit(request)
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

                # 同步供应商（多数图片厂商）在 submit 里就等到了结果，
                # 这里拿到的是终态快照而不是任务 ID
                resolved_id: str
                sync_result: GenerationProgress | None
                if isinstance(delivered, GenerationProgress):
                    sync_result = delivered
                    resolved_id = delivered.external_id
                else:
                    sync_result = None
                    resolved_id = delivered

                if not await store.mark_submitted(record_id, resolved_id):
                    # 提交期间被取消：供应商那边可能已经受理（钱照花），
                    # 但不能再发「已提交」事件、也不能继续轮询把结果写回来
                    logger.info(
                        "记录已终态，丢弃提交结果 kind=%s id=%s external=%s",
                        kind,
                        record_id,
                        resolved_id,
                    )
                    return
                await session.commit()
                await self._emit(
                    EVT_GENERATION_SUBMITTED,
                    kind=kind,
                    record_id=record_id,
                    owner_id=record.owner_id,
                    model=record.model,
                    external_id=resolved_id,
                )

                if sync_result is not None and sync_result.state.terminal:
                    # 已经出结果，再排一次轮询只是白跑一跳
                    await self._settle(
                        store=store,
                        session=session,
                        kind=kind,
                        record_id=record_id,
                        owner_id=record.owner_id,
                        model=record.model,
                        result=sync_result,
                    )
                    return

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

                if self._timed_out(record):
                    # 墙钟预算优先于轮询次数：max_polls 是「本次进程内」的计数，
                    # 重启会归零（见 resume 的注释），反复重启时它永远到不了上限
                    message = "生成超时，请重试"
                    if await store.mark_failed(record_id, message):
                        await session.commit()
                        await self._emit(
                            EVT_GENERATION_FAILED,
                            kind=kind,
                            record_id=record_id,
                            owner_id=record.owner_id,
                            error=message,
                        )
                    return

                if polls >= settings.generation_max_polls:
                    # 事件里的 error 与落库文案必须一致：接上通知渠道后，
                    # 用户收到的说法不能和任务详情页不一样（R4-10）
                    message = "生成超时，请重试"
                    if await store.mark_failed(record_id, message):
                        await session.commit()
                        await self._emit(
                            EVT_GENERATION_FAILED,
                            kind=kind,
                            record_id=record_id,
                            owner_id=record.owner_id,
                            error=message,
                        )
                    return

                resolver = getattr(pipeline, "resolve_provider", None)
                provider = (
                    resolver(record.model) if resolver is not None
                    else get_model_router().provider_for(record.model)
                )
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
                        output_url = await self._persist(kind, record_id, result.output_url)
                        cover_url = self._persisted_cover(result, output_url)
                        # 终态保护必须接返回值：轮询期间用户点了取消时，
                        # `mark_succeeded` 会拒绝写入（记录已是 cancelled）。
                        # 此时继续 commit + 归档 + 发事件，用户就会先看到
                        # 「已取消」再收到「生成成功」，草稿箱还多一条脏数据。
                        if not await store.mark_succeeded(
                            record_id,
                            output_url=output_url,
                            cover_url=cover_url,
                            progress=result.progress or 100,
                        ):
                            logger.info(
                                "记录已终态，丢弃轮询结果 kind=%s id=%s", kind, record_id
                            )
                            return
                        await session.commit()
                        # 生成成功是既成事实，先落库再往资产库送：入库失败不该
                        # 把用户已经看到的结果改回失败（见 `_archive`）。
                        await self._archive(kind, session, record_id, output_url)
                        # 落库存 key（与存储驱动无关），事件给地址（订阅方要能直接用）
                        public_output, public_cover = await artifact_urls(
                            [output_url, cover_url]
                        )
                        await self._emit(
                            EVT_GENERATION_SUCCEEDED,
                            kind=kind,
                            record_id=record_id,
                            owner_id=record.owner_id,
                            model=record.model,
                            output_url=public_output,
                            cover_url=public_cover,
                        )
                        return

                    if state == GenerationState.FAILED:
                        if result.retryable and polls + 1 < settings.generation_max_polls:
                            next_delay = self._backoff(polls)
                        else:
                            if not await store.mark_failed(
                                record_id, (result.error or "生成失败")[:_MAX_ERROR_LEN]
                            ):
                                logger.info(
                                    "记录已终态，丢弃失败结果 kind=%s id=%s",
                                    kind,
                                    record_id,
                                )
                                return
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
                        if result.progress != record.progress and await store.mark_progress(
                            record_id, result.progress
                        ):
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
    async def _persist(self, kind: str, record_id: str, url: str | None) -> str | None:
        """把供应商产物落到自己的存储上（可选钩子 `pipeline.persist_output`）。

        没有这个钩子时原样返回 —— 视频那类「供应商直链长期有效」的能力不必落盘。
        """
        if not url:
            return url
        persist = getattr(get_pipeline(kind), "persist_output", None)
        if persist is None:
            return url
        try:
            return await persist(record_id, url)
        except Exception:  # noqa: BLE001 - 落盘失败不该把成功改成失败
            logger.exception("产物落盘失败，退回供应商 URL kind=%s id=%s", kind, record_id)
            return url

    async def _archive(
        self, kind: str, session: AsyncSession, record_id: str, stored: str | None
    ) -> None:
        """把生成结果送进资产库草稿箱（可选钩子 `pipeline.archive_request`）。

        这是「AI 生成的图 / 视频先进草稿箱，本人确认后才进库」的唯一入口。
        没有这一步，资产库的草稿箱永远是个空架子：`POST /assets/from-generation`
        与 `library-ingest` 端口都建好了，但没有任何人调用它们（2026-11-10 查出的
        线上问题——用户生成完图去草稿箱，一条都没有）。

        **为什么是钩子而不是框架里写死字段映射**：`image_jobs.size`、
        `video_tasks.aspect_ratio` 分别怎么对应入库快照，是各业务模块自己的事；
        框架连它们的 ORM 实体都不认识，硬写一张映射表就是把三个模块的知识
        塞回框架里。模块不实现这个钩子 = 它的产物不进草稿箱，语义明确。

        **失败只记日志**：此刻生成已经 commit 成功，用户界面上就是「生成完成」。
        这里抛异常会把它改成失败，用户会以为图没了；静默吞掉又无法排查，所以
        留一条带 `kind/id` 的 error 日志，并保持 `source_key` 幂等——同一个任务
        再跑一次（重试、恢复）不会产生第二行草稿。
        """
        if not stored:
            return
        pipeline = get_pipeline(kind)
        build = getattr(pipeline, "archive_request", None)
        if build is None:
            return
        if not has_library_ingest():
            # 生成与资产库可以分别部署（资产库还没上线的环境）。库不在时打断
            # 生成是更坏的选择：用户连生成都用不了。留痕，等库上线后由重试补齐。
            logger.warning(
                "资产库入库端口未注册，生成结果不落草稿箱 kind=%s id=%s", kind, record_id
            )
            return
        try:
            record = await pipeline.store(session).load(record_id)
            if record is None:
                return
            request = await build(session, record, stored)
            if request is None:
                return
            result = await get_library_ingest().ingest_generated(session, request)
        except Exception:  # noqa: BLE001 - 见 docstring：生成成功不能被入库失败反噬
            logger.exception("生成结果入库失败 kind=%s id=%s", kind, record_id)
            # 入库实现自己 commit / rollback 过；剩下没提交的半截语句在这里收尾，
            # 否则后续同一会话里的查询会撞在「事务已被中止」上。
            with contextlib.suppress(Exception):
                await session.rollback()
            return
        logger.info(
            "生成结果已入草稿箱 kind=%s id=%s asset=%s created=%s",
            kind,
            record_id,
            result.asset_id,
            result.created,
        )

    @staticmethod
    def _persisted_cover(
        result: GenerationProgress, stored: str | None
    ) -> str | None:
        """封面和产物是同一张图时，封面也跟着换成落盘地址。

        图片类能力的 `cover_url` 往往就是 `output_url`（供应商返回同一个签名地址），
        `persist_output` 只搬了产物——不跟着换，前端拿 `coverUrl` 渲染的卡片会在
        签名过期后裂图，而 `outputUrl` 明明是好的。两者不同（视频封面单独一张）时不碰，
        因为那张图没有落盘，换过去等于指向一个不存在的地址。
        """
        cover = result.cover_url
        if cover and result.output_url and stored and cover == result.output_url:
            return stored
        return cover

    async def _settle(
        self,
        *,
        store: GenerationStore,
        session: AsyncSession,
        kind: str,
        record_id: str,
        owner_id: str,
        model: str,
        result: GenerationProgress,
    ) -> None:
        """同步供应商直接给终态：落结果 + 发事件，不排轮询。

        与 `handle_poll` 的收尾逻辑必须保持一致 —— 两条路径写出的事件字段
        和错误文案要是不同的，前端会出现「同一件事两种说法」。
        """
        # 终态保护（R11-02 现象 C）：并发下可能已经有另一个 worker 把这条记录
        # 判成 failed（典型是巡检误伤在途任务）。此时再落 succeeded 会让用户
        # 先收到失败、再收到成功，状态与事件来回跳。这里以库里的终态为准，
        # 让用户走显式重试，而不是让一条记录自称成功又自称失败。
        # 用独立会话读：当前 session 的 identity map 可能还缓存着本次进程写入的
        # `running`，看不到另一个 worker 已经提交的 `failed`。
        async with get_session_factory()() as check_session:
            current = await get_pipeline(kind).store(check_session).load(record_id)
        if current is None or current.terminal:
            logger.warning(
                "记录已终态或不存在，跳过结算 kind=%s id=%s status=%s",
                kind,
                record_id,
                None if current is None else current.status,
            )
            return
        if result.state == GenerationState.SUCCEEDED:
            # 供应商给的 URL 多半会过期（签名地址），能不能落盘由 pipeline 自己决定。
            # 落盘失败不改变「生成成功」这个事实，只退回原 URL 并留一条警告。
            output_url = await self._persist(kind, record_id, result.output_url)
            cover_url = self._persisted_cover(result, output_url)
            if not await store.mark_succeeded(
                record_id,
                output_url=output_url,
                cover_url=cover_url,
                progress=result.progress or 100,
            ):
                logger.info("记录已终态，跳过结算 kind=%s id=%s", kind, record_id)
                return
            await session.commit()
            await self._archive(kind, session, record_id, output_url)
            public_output, public_cover = await artifact_urls([output_url, cover_url])
            await self._emit(
                EVT_GENERATION_SUCCEEDED,
                kind=kind,
                record_id=record_id,
                owner_id=owner_id,
                model=model,
                output_url=public_output,
                cover_url=public_cover,
            )
            return

        message = (result.error or "生成失败")[:_MAX_ERROR_LEN]
        if not await store.mark_failed(record_id, message):
            logger.info("记录已终态，跳过结算 kind=%s id=%s", kind, record_id)
            return
        await session.commit()
        await self._emit(
            EVT_GENERATION_FAILED,
            kind=kind,
            record_id=record_id,
            owner_id=owner_id,
            error=message,
        )

    @staticmethod
    def _timed_out(record: GenerationRecord) -> bool:
        """按本轮执行起点判定墙钟超时；未指定起点的业务沿用创建时间。

        `generation_timeout_sec` 之前没有任何代码读取：真实预算等于
        `max_polls × poll_interval`（默认 150×2 = 300 秒），与配置里写的
        1800 秒差了 6 倍 —— 一个 5 分钟的视频会在第 5 分钟被判「生成超时」。
        """
        started = record.timeout_started_at or record.created_at
        if started is None:
            return False
        created = started
        if created.tzinfo is None:  # sqlite 读回来是 naive，按 UTC 解释
            created = created.replace(tzinfo=UTC)
        return (datetime.now(tz=UTC) - created).total_seconds() > settings.generation_timeout_sec

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
                if not await store.mark_failed(record_id, message):
                    return
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
        if not await store.mark_failed(record_id, message):
            # 记录已被取消：不要再发失败事件，否则用户收到的是「已取消」+「失败」
            logger.info("记录已终态，跳过失败收尾 kind=%s id=%s", kind, record_id)
            return
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
