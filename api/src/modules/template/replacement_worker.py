"""上传准备和下载为非计费阶段；提交、轮询、超时与事件复用生成框架。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from api.accounts import account_is_active
from core.config import settings
from core.exceptions import Conflict, NotFound, UpstreamError
from core.logging import get_logger
from generation.runner import get_runner
from infra import get_queue
from infra.ai.libtv import execute_stage
from infra.queue.base import Job
from modules.template.replacement_pipeline import KIND_REPLACEMENT
from modules.template.replacement_repository import ReplacementManifest, ReplacementRepository
from workers import register_handler

logger = get_logger(__name__)
RETRYABLE = {
    "prepared",
    "waiting_free",
    "needs_login",
    "service_error",
    "waiting_executor",
    "failed",
}

# 自动重试的节奏与上限。
#
# 为什么必须有上限：`max_attempts=1` 意味着失败时**新建一个 job**，而每次新建都会
# 重新排队。一个卡在执行器上的 run 因此会「每 30 秒唤醒一次、打一次上游」，永不停止，
# 直到有人发现。上限 + 退避把「无限重试」变成「五次之内解决，否则转人工」。
MAX_RETRIES = 5
RETRY_BASE_SEC = 30.0
RETRY_MAX_SEC = 600.0


def backoff_delay(count: int) -> float:
    """第 `count` 次重试前该等多久：30s 起，每次翻倍，封顶 10 分钟。"""
    return min(RETRY_BASE_SEC * 2 ** max(count - 1, 0), RETRY_MAX_SEC)


async def owner_disabled(owner_id: str) -> bool:
    """任务发起人是否已被停用。

    HTTP 那一半已经断了：停用会吊销全部会话（`_cut_access`），这个人再也调不了接口。
    但后台任务走的是文件里的 `owner_id`，不经过鉴权——没有这一道，「停用」只是把人
    关在门外，队列里排着的活照样替他跑、照样扣积分。

    这条**只挡新提交**：已经拿到 `task_id` 的 run 继续轮询与下载——那笔钱已经花出去了，
    停掉只会让成片烂在共享盘里。判断放在 `task_id` 分支之后就是这个意思。

    查不到用户（历史档案、测试夹具里的合成 owner）按「不拦」处理：这里的目的是
    关掉「离职后还在花钱」的口子，不是给所有历史数据补一道会误伤的闸门。

    走 `api.accounts` 这个注入点而不是直接 import identity：业务模块之间禁止互相
    import（`tests/test_architecture.py` 守着），而这里需要的只是一个 Bool。
    """
    return await account_is_active(owner_id) is False


def _bump_retry(
    repo: ReplacementRepository,
    path: Path,
    meta: ReplacementManifest,
    *,
    reason: str,
) -> float | None:
    """记一次自动重试；额度用完返回 None（同时把 run 标成 service_error）。

    `reason` 会写进 `last_error`：让人一眼看出「这是自动放弃，不是上游报错」，
    以及该去找谁（执行器占用 / 下载失败）。
    """
    meta.retry_count += 1
    if meta.retry_count > MAX_RETRIES:
        meta.status = "service_error"
        meta.error_code = "RETRY_EXHAUSTED"
        meta.last_error = f"{reason}：已自动重试 {MAX_RETRIES} 次仍失败，转人工处理"
        repo.save(path, meta)
        logger.warning("替换任务重试耗尽 run=%s reason=%s", meta.id, reason)
        return None
    repo.save(path, meta)
    return backoff_delay(meta.retry_count)



async def enqueue(run_id: str, *, delay: float = 0) -> None:
    await get_queue().enqueue(
        Job(
            name="template.replacement.prepare",
            payload={"id": run_id},
            max_attempts=1,
            idempotency_key=f"replacement:prepare:{run_id}",
            timeout_sec=1600,
        ),
        delay=delay,
    )


def schedule_state(run_id: str, owner: str) -> str:
    repo = ReplacementRepository()
    with repo.edit(run_id) as meta:
        if meta.owner_id != owner:
            raise NotFound("调用档案不存在")
        if meta.status == "success":
            return "done"
        if not meta.task_id and (meta.status == "submission_unknown" or meta.submit_attempts > 0):
            raise Conflict("提交回执未知，禁止重提，请先核对上游记录")
        if meta.task_id:
            meta.framework_status = "running"
            return "poll"
        if meta.status in ("preparing", "prepare_failed"):
            raise Conflict("输入尚未准备完成，请先使用原配置继续准备")
        if meta.status not in RETRYABLE and meta.status not in ("queued", "uploading", "uploaded"):
            raise Conflict("该任务当前不能提交")
        meta.status = "queued"
        meta.last_error = None
        meta.framework_status = "queued"
        # 人工再点「继续」= 重新给一份重试额度：否则上一轮耗尽后，这次刚入队就又
        # 在第一次失败时被判「已重试 5 次」，看起来像按钮没反应。
        meta.retry_count = 0
        return "prepare"


async def schedule(run_id: str, owner: str) -> None:
    if not settings.worker_enabled:
        raise UpstreamError("后台执行器未启用")
    action = await asyncio.to_thread(schedule_state, run_id, owner)
    if action == "done":
        return
    if action == "poll":
        await get_runner().resume(KIND_REPLACEMENT, run_id)
        return
    try:
        await enqueue(run_id)
    except Exception:
        logger.exception("准备任务入队失败 run_id=%s", run_id)
        await asyncio.to_thread(mark_schedule_failure, run_id)
        raise UpstreamError("准备调度失败，请稍后继续") from None


def mark_schedule_failure(run_id: str) -> None:
    try:
        with ReplacementRepository().edit(run_id) as meta:
            if meta.status == "queued" and not meta.task_id and not meta.submit_attempts:
                meta.status = "service_error"
                meta.last_error = "准备调度失败，请继续；尚未提交"
    except Conflict:
        # 可能已被其他工作进程领取，禁止覆盖其在途状态。
        logger.info("准备任务已由其他执行器接手 run_id=%s", run_id)


@register_handler("template.replacement.prepare")
async def prepare_upload(job: Job) -> None:
    run_id = str(job.payload["id"])
    repo = ReplacementRepository()
    path = await asyncio.to_thread(repo.path, run_id)
    meta = await asyncio.to_thread(repo.load, path)
    if meta.task_id:
        await get_runner().resume(KIND_REPLACEMENT, run_id)
        return
    if meta.status in ("submitting", "submission_unknown") or meta.submit_attempts:
        return
    # 停用账号不再往回推进：这里之后就是「上传 + 提交」，也就是真正花钱的地方。
    if await owner_disabled(meta.owner_id):
        meta.status = "service_error"
        meta.error_code = "OWNER_DISABLED"
        meta.last_error = "账号已停用，任务不再继续；恢复账号后可手动继续"
        await asyncio.to_thread(repo.save, path, meta)
        logger.warning("发起人已停用，替换任务停止准备 run=%s owner=%s", run_id, meta.owner_id)
        return
    result = await execute_stage(run_id, "upload")
    meta = await asyncio.to_thread(repo.load, path)
    if result.code == "EXECUTOR_BUSY":
        meta.status = "waiting_executor"
        meta.last_error = result.message
        delay = await asyncio.to_thread(
            _bump_retry, repo, path, meta, reason="执行器持续被占用"
        )
        if delay is None:
            return
        await enqueue(run_id, delay=delay)
        return
    if result.code != "OK" or meta.status not in ("uploaded", "waiting_free"):
        meta.status = "service_error"
        meta.error_code = result.message or result.code
        meta.last_error = f"上传准备未完成：{meta.error_code}"
        await asyncio.to_thread(repo.save, path, meta)
        return
    meta.preparation_ready = True
    meta.retry_count = 0
    if meta.status == "waiting_free":
        await asyncio.to_thread(repo.save, path, meta)
        return
    meta.framework_status = "queued"
    await asyncio.to_thread(repo.save, path, meta)
    try:
        await get_runner().dispatch(KIND_REPLACEMENT, run_id)
    except Exception:
        logger.exception("生成调度失败 run_id=%s", run_id)
        meta.status = "service_error"
        meta.last_error = "生成调度失败，请继续；尚未提交"
        await asyncio.to_thread(repo.save, path, meta)
        raise UpstreamError("生成调度失败，请继续") from None


@register_handler("libtv.download")
async def download(job: Job) -> None:
    run_id = str(job.payload["id"])
    result = await execute_stage(run_id, "download")
    if result.code != "OK":
        logger.warning("成片下载待恢复 run_id=%s code=%s", run_id, result.code)
        repo = ReplacementRepository()
        try:
            path = await asyncio.to_thread(repo.path, run_id)
            meta = await asyncio.to_thread(repo.load, path)
        except NotFound:
            # 档案已被清掉：继续排下去只会每 30 秒报一次同样的错。
            logger.info("成片下载放弃：调用档案已不存在 run_id=%s", run_id)
            return
        delay = await asyncio.to_thread(
            _bump_retry, repo, path, meta, reason="成片下载失败"
        )
        if delay is None:
            return
        await get_queue().enqueue(
            Job(
                name="libtv.download",
                payload={"id": run_id},
                max_attempts=1,
                idempotency_key=f"libtv:download:{run_id}",
                timeout_sec=300,
            ),
            delay=delay,
        )


async def enqueue_recovery(run_id: str, *, delay: float = 0) -> None:
    await get_queue().enqueue(
        Job(
            name="template.replacement.recover",
            payload={"id": run_id},
            max_attempts=1,
            idempotency_key=f"replacement:recover:{run_id}",
        ),
        delay=delay,
    )


def recover_state(run_id: str) -> str:
    repo = ReplacementRepository()
    with repo.edit(run_id) as meta:
        if meta.task_id and meta.status != "success":
            meta.framework_status = "running"
            return "poll"
        if meta.status == "submitting" or (not meta.task_id and meta.submit_attempts):
            meta.status = "submission_unknown"
            meta.framework_status = "failed"
            meta.last_error = "提交中断，请先核对上游回执；禁止自动重提"
        elif meta.status in ("queued", "uploading", "uploaded", "waiting_executor"):
            return "prepare"
    return "done"


@register_handler("template.replacement.recover")
async def recover(job: Job) -> None:
    run_id = str(job.payload["id"])
    try:
        action = await asyncio.to_thread(recover_state, run_id)
    except Conflict:
        # API 重启时监督进程可能仍在提交；只等回执，绝不覆盖其状态或重提。
        await enqueue_recovery(run_id, delay=30)
        return
    if action == "poll":
        await get_runner().resume(KIND_REPLACEMENT, run_id)
    elif action == "prepare":
        await enqueue(run_id)


async def resume() -> None:
    if settings.app_env == "test" or not settings.worker_enabled:
        return
    repo = ReplacementRepository()
    try:
        await asyncio.to_thread(repo.repair_indexes)
        paths = await asyncio.to_thread(repo.all_paths)
    except (UpstreamError, OSError):
        logger.warning("共享盘暂不可用，替换任务等待共享盘恢复")
        return
    for path in paths:
        meta = await asyncio.to_thread(repo.load, path)
        if (
            meta.task_id
            or meta.submit_attempts
            or meta.status in ("queued", "uploading", "uploaded", "waiting_executor", "submitting")
        ):
            await enqueue_recovery(meta.id)
