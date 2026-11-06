"""上传准备和下载为非计费阶段；提交、轮询、超时与事件复用生成框架。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from core.config import settings
from core.exceptions import Conflict, NotFound, Unavailable, UpstreamError
from core.logging import get_logger
from core.ports.accounts import account_is_active
from generation.runner import get_runner
from infra import get_queue
from infra.ai.libtv import execute_stage
from infra.media_catalog import MediaCatalog, catalog_lease
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

# 恢复器的两个状态集合（R14-02）。分开写而不是一个 if 串，是因为它们回答的是
# 两个不同的问题：这些**没有恢复空间**（入队只会白跑），那些**还需要推进**。
SETTLED_STATUSES = frozenset({"success", "failed", "service_error", "submission_unknown"})
UNFINISHED_STATUSES = frozenset(
    {"queued", "uploading", "uploaded", "waiting_executor", "submitting"}
)

# 自动重试的节奏与上限。
#
# 为什么必须有上限：`max_attempts=1` 意味着失败时**新建一个 job**，而每次新建都会
# 重新排队。一个卡在执行器上的 run 因此会「每 30 秒唤醒一次、打一次上游」，永不停止，
# 直到有人发现。上限 + 退避把「无限重试」变成「五次之内解决，否则转人工」。
MAX_RETRIES = 5
RETRY_BASE_SEC = 30.0
RETRY_MAX_SEC = 600.0

# 「提交回执未知」的两种文案：一个解释为什么拦，一个解释怎么放行。
# 和 `image` 的 `SUBMIT_UNKNOWN_MESSAGE` 同一口径——文案承诺「核对后可重试」，
# 就必须真有一个出口能重试（R11-02 / R14-01）。
SUBMISSION_UNKNOWN = "提交回执未知，禁止重提，请先核对上游记录"
SUBMISSION_UNKNOWN_CONFIRM = (
    "上一次提交可能已产生费用（上游任务号没有落库）：请先凭上游记录核对；"
    "确认要重新提交时带 confirmCharged=true"
)


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

    走 `core.ports.accounts` 这个注入点而不是直接 import identity：业务模块之间禁止互相
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


def set_billing_mode(run_id: str, owner: str, mode: str) -> None:
    """切换这一条任务的计费模式；已交给上游的任务不允许再改。

    `credits` 只影响本任务：提交时关掉账号的 0 积分权益标签走正常积分队列，
    **不动账号级开关**（billing-default），同账号的其他人下次提交仍走免费队列。
    这也是 legacy 文档推荐的做法——账号开关不等于某次任务的费用保证。
    """
    if mode not in ("free", "credits"):
        raise Conflict("计费模式无效")
    repo = ReplacementRepository()
    with repo.edit(run_id) as meta:
        if meta.owner_id != owner:
            raise NotFound("调用档案不存在")
        # 已提交上游 = 队列锁定，但「再点一次确认」不该被拦成错误：
        # 前端重新提交时会带上档案里的模式，那不是切换，用户要的只是看进度。
        # 只有「把一条已在免费队列的任务改成积分」才是真冲突，因为它要花钱。
        if meta.task_id and mode == "credits" and meta.billing_mode != "credits":
            raise Conflict(
                "这条任务已在上游免费队列中，计费队列不能再改；"
                "继续执行只会查询进度。如需用积分生成，请新建一条任务再勾选加速"
            )
        if meta.task_id or meta.billing_mode == mode:
            return
        meta.billing_mode = mode
        # 旧报价属于另一条队列：免费队列报 0、积分队列报正数，沿用它只会误导核对。
        meta.estimated_credits = None
        # 免费队列只有在报价非 0 时才会卡在 waiting_free；换到积分队列后应当继续准备，
        # 否则点了加速仍然停在原地，看起来像按钮没反应。
        if mode == "credits" and meta.status == "waiting_free":
            meta.status = "uploaded"
            meta.framework_status = "queued"
            meta.last_error = None


def schedule_state(run_id: str, owner: str) -> str:
    repo = ReplacementRepository()
    with repo.edit(run_id) as meta:
        if meta.owner_id != owner:
            raise NotFound("调用档案不存在")
        if meta.status == "success":
            return "done"
        if not meta.task_id and (meta.status == "submission_unknown" or meta.submit_attempts > 0):
            raise Conflict(SUBMISSION_UNKNOWN)
        if meta.task_id:
            meta.framework_status = "running"
            return "poll"
        if meta.status in ("preparing", "prepare_failed"):
            raise Conflict("输入尚未准备完成，请先使用原配置继续准备")
        if meta.status not in RETRYABLE and meta.status not in ("queued", "uploading", "uploaded"):
            raise Conflict("该任务当前不能提交")
        # 提交前核对素材仍可用：原片被移入回收站、参考素材被删、封装被撤版，
        # 都会让上游白跑一趟并扣费。锁只覆盖「读快照 → 置 queued」这段窗口。
        with catalog_lease(repo.storage):
            MediaCatalog(repo.storage).check_run(repo.path(run_id))
        meta.status = "queued"
        meta.last_error = None
        meta.framework_status = "queued"
        # 人工再点「继续」= 重新给一份重试额度：否则上一轮耗尽后，这次刚入队就又
        # 在第一次失败时被判「已重试 5 次」，看起来像按钮没反应。
        meta.retry_count = 0
        return "prepare"


def reconfirm_state(
    run_id: str, owner: str, *, confirm_charged: bool
) -> tuple[ReplacementManifest, int]:
    """用户确认「上一次可能已经花了钱，我仍要重新提交」后的复位。

    与 `image` 的 `POST /images/{id}/retry?confirmCharged=` 同一个语义，也同一个
    理由：`submission_unknown` 的定义就是「提交动作可能已经成功」，没有出口时
    这条记录只能靠运维进共享盘手改 `run.json`（R14-01）。三条路都必须放行：
    `schedule_state` 会拦、`prepare_upload` 会直接 return、`recover_state` 会
    定格在 `submission_unknown`——所以清零 `submit_attempts` 是唯一的钥匙。

    复位后仍走 `schedule()`，不在这里直接提交：提交只有一条路径，重试额度、
    执行器占用、停用账号这些判断都长在那条路上。
    """
    repo = ReplacementRepository()
    with repo.edit(run_id) as meta:
        if meta.owner_id != owner:
            raise NotFound("调用档案不存在")
        if meta.task_id:
            # 已有回执：该走「继续」续查询，而不是重提一遍（重提就是第二次计费）
            raise Conflict("该调用已有上游任务号，请直接继续查询结果")
        if meta.status != "submission_unknown" and not meta.submit_attempts:
            raise Conflict("当前档案不在「提交回执未知」状态，无需确认重提")
        if not confirm_charged:
            raise Conflict(SUBMISSION_UNKNOWN_CONFIRM)
        # 复位前留一份原样：审计要说清「清掉了几次提交意图」，否则事后只看得到 0
        previous_attempts = meta.submit_attempts
        meta.submit_attempts = 0
        meta.status = "queued"
        meta.framework_status = "queued"
        meta.last_error = None
        meta.error_code = None
        meta.retry_count = 0
        return meta, previous_attempts


async def schedule(run_id: str, owner: str) -> None:
    if not settings.worker_enabled:
        # 配置状态，不是上游故障：503 让前端与运维都不会去查 LibTV（T5）
        raise Unavailable("后台执行器未启用（WORKER_ENABLED=false），请联系运维开启")
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
        before_status = meta.status
        meta.status = "service_error"
        # `result.code` 的默认值就是 "OK"，直接拿来当错误码会让页面显示
        # 「上传准备未完成：OK」（R14 💭）。只有真出错才带上游码，否则是
        # 「脚本说成功、档案却没走到 uploaded」这种状态不一致。
        meta.error_code = result.code if result.code != "OK" else "UPLOAD_INCOMPLETE"
        meta.last_error = (
            f"上传准备未完成：{result.message or result.code}（档案状态 {before_status}）"
        )
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
    """进程启动恢复：只挑「还有事可做」的档案进队列。

    R14-02：原来用 `meta.task_id` 做条件，命中**所有**历史成功记录——每次启动
    都白投一次、白读两次盘；更糟的是本地已判失败的记录会被 `recover_state`
    翻回 `framework_status=running` 并真的再查一次上游，集合永不收敛。
    终态与「等人确认」的状态一律跳过，收敛才成立。
    """
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
        if meta.status in SETTLED_STATUSES:
            continue
        if meta.task_id or meta.submit_attempts or meta.status in UNFINISHED_STATUSES:
            await enqueue_recovery(meta.id)
