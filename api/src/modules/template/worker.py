"""替换运行的队列作业：上传准备、成片下载、恢复。

**这里最容易被重构改坏的一条**：`max_attempts=1` 意味着失败时是**新建一个 job**，
而每次新建都会重新排队。一个卡在执行器上的 run 因此会「每 30 秒唤醒一次、打一次
上游」，永不停止，直到有人发现。所以自动重试必须有上限 + 退避（`_bump_retry`），
把「无限重试」变成「五次之内解决，否则转人工」。

数据搬到数据库之后，`run.json` 的角色变了：它从**事实来源**降为**给执行器的投影**。
数据库是权威，`run.json` 由本模块单向写下去（`write_state`），执行器的回执再读回来
（`read_state`）。这样执行器（`bridge.mjs` / `libtv_process`）一行都不用改。
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from core.config import settings
from core.exceptions import Conflict, NotFound, Unavailable, UpstreamError
from core.logging import get_logger
from core.metrics import METRICS
from core.ports.accounts import account_is_active
from generation.runner import get_runner
from infra import get_queue
from infra.ai.libtv import execute_stage
from infra.archive import get_archive_storage
from infra.archive.base import lease
from infra.queue.base import Job
from modules.template.authorization import authorization_gap
from modules.template.channels import CHANNEL_MODELS, run_channel
from modules.template.models import ReplacementRun
from modules.template.pipeline import KIND_REPLACEMENT
from modules.template.repository import RunRepository
from modules.template.run_clock import begin, set_status
from modules.template.workspace import input_checksums, read_state, run_path, write_state
from workers import register_handler

logger = get_logger(__name__)

# 可以重新提交的状态：`failed` / `service_error` 是「上一轮输了」，允许重来。
RETRYABLE = {
    "prepared",
    "waiting_free",
    "needs_login",
    "service_error",
    "waiting_executor",
    "failed",
}

# 恢复器的两个集合分开写，因为它们回答两个不同问题：
# 这些**没有恢复空间**（入队只会白跑），那些**还需要推进**。
SETTLED_STATUSES = frozenset({"success", "failed", "service_error", "submission_unknown"})
# `running` / `submitted` / `generated` 是「上游还在跑」的中间态：进程重启后
# 它们必须重新进入轮询，否则任务会永远卡在旧进度上，只能靠人工点一次「继续」。
# 这几种状态一定带 task_id，recover_state 会返回 poll —— 只查询、不重提、不扣费。
UNFINISHED_STATUSES = frozenset(
    {
        "queued", "uploading", "uploaded", "waiting_executor", "submitting",
        "preparing", "prepared", "submitted", "running", "generated",
    }
)

MAX_RETRIES = 5
RETRY_BASE_SEC = 30.0
RETRY_MAX_SEC = 600.0

RECOVER_LOCK_MAX_RETRIES = 6
RECOVER_LOCK_BASE_SEC = 30.0
RECOVER_LOCK_MAX_SEC = 600.0

RECOVERY_LOCK_GIVEUPS = METRICS.counter(
    "replacement_recovery_lock_giveups_total",
    "替换任务恢复因抢不到档案锁而放弃等待的次数",
)

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

    HTTP 那一半已经断了（停用会吊销全部会话），但后台任务走的是记录里的
    `owner_id`，不经过鉴权——没有这一道，「停用」只是把人关在门外，队列里排着的
    活照样替他跑、照样扣积分。**只挡新提交**：已拿到 `task_id` 的 run 继续轮询与
    下载，那笔钱已经花出去了。查不到用户按「不拦」处理，避免误伤历史合成数据。
    """
    return await account_is_active(owner_id) is False


def _sync_out(run: ReplacementRun, path) -> None:
    """把数据库行投影成执行器认识的 `run.json`。只写不读。

    `file_checksums` 从 `inputs/input-map.json` 派生：执行器会拿它跟实算摘要比，
    缺这一项 bridge 在 upload 阶段抛 `Cannot read properties of undefined`。
    老档案（清单缺失）保留 `run.json` 里已有的那份，别把执行器要的字段洗掉。
    """
    checksums = input_checksums(path) or read_state(path).get("file_checksums") or {}
    write_state(
        path,
        {
            "file_checksums": checksums,
            "id": run.id,
            "status": run.status,
            # **字段名必须是 snake_case**：执行器（`bridge.mjs`）读的是
            # `meta.task_id`，写成 `taskId` 会让它认为「这条还没提交过」，
            # 于是 `canSubmit()` 放行、重复提交重复计费；`poll` 也会报
            # 「No task ID」，成片永远下不来。投影的存在意义就是喂给执行器，
            # 名字要对齐消费方，不是对齐 Python 习惯。
            "task_id": run.task_id,
            "submit_attempts": run.submit_attempts,
            "framework_status": run.framework_status,
            "created_at": run.created_at.isoformat(),
            "progress_percent": run.progress_percent,
            "estimated_credits": run.estimated_credits,
            "credits": run.credits,
            "last_error": run.last_error,
            "error_code": run.error_code,
            "billing_mode": run.billing_mode,
            "duration": run.duration,
            "kind": run.kind,
            "target": run.target,
        },
    )


def sync_projection(run: ReplacementRun) -> None:
    """把数据库行投影进工作区（`run.json`）。

    公开这一个入口给「准备」用：准备阶段也产生新状态（`prepared`），执行器接手
    之前必须能看到。业务层只写不读，读回来仍走 `_absorb`。
    """
    _sync_out(run, run_path(run.id))


def _absorb(run: ReplacementRun, path) -> None:
    """把执行器的回执读回数据库。

    **只吸收执行器有权威的字段**（status / task_id / 提交计数 / 进度 / 估价 /
    实际扣费），业务字段（owner、pack、deleted_at）一个都不碰：执行器不该能改这些。

    积分队列的真实扣费由 `submitCredits` 从提交回执写进 `run.json`；不吸收
    `credits` 的话前端永远显示「待核对账单」（`test_libtv_billing` 覆盖）。
    执行器侧的 poll 已保证不会拿排队阶段的 0 覆盖这笔钱，会回落到 submission.json。
    """
    state = read_state(path)
    if not state:
        return
    status = state.get("status")
    if isinstance(status, str) and status:
        run.status = status
    task_id = state.get("taskId") or state.get("task_id")
    if isinstance(task_id, str) and task_id:
        run.task_id = task_id
    attempts = state.get("submitAttempts") or state.get("submit_attempts")
    if isinstance(attempts, int):
        run.submit_attempts = attempts
    progress = state.get("progress_percent")
    if isinstance(progress, int):
        run.progress_percent = progress
    estimated = state.get("estimated_credits")
    if isinstance(estimated, (int, float)):
        run.estimated_credits = float(estimated)
    charged = state.get("credits")
    if isinstance(charged, (int, float)) and not isinstance(charged, bool):
        run.credits = float(charged)
    last_error = state.get("last_error")
    if isinstance(last_error, str):
        run.last_error = last_error
    error_code = state.get("error_code")
    if isinstance(error_code, str):
        run.error_code = error_code


def receipt_power(run_id: str) -> float | None:
    """提交回执里的结算积分，是「这次到底花了多少」的权威来源。

    `run.json` 的 credits 会被下载阶段的 vendor poll 用 `progress.power` 覆盖
    （排队/生成阶段常是 0 或 null），而框架的 GenerationProgress 又不带计费字段，
    数据库因此长期显示「待核对账单」。只在结算后的回执里取数，不猜、不累积。
    """
    try:
        payload = json.loads(
            (run_path(run_id) / "records/submission.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    value = (payload.get("data") or {}).get("power")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


async def sync_credit_charge(run_id: str) -> None:
    """下载完成后把真实扣费写回数据库，供任务中心/档案列表展示。"""
    from db.session import get_session_factory

    charged = await asyncio.to_thread(receipt_power, run_id)
    if charged is None:
        return
    async with get_session_factory()() as session:
        repo = RunRepository(session)
        run = await repo.get(run_id)
        if run is None:
            return
        run.credits = charged
        await repo.save(run)
        await session.commit()


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


async def set_billing_mode(session, run_id: str, owner: str, mode: str) -> None:
    """切换这一条任务的计费模式；已交给上游的任务不允许再改。

    `credits` 只影响本任务：提交时关掉账号的 0 积分权益标签走正常积分队列，
    **不动账号级开关**，同账号其他人下次提交仍走免费队列。
    """
    if mode not in ("free", "credits"):
        raise Conflict("计费模式无效")
    repo = RunRepository(session)
    run = await repo.get_or_404(run_id, owner=owner)
    if run_channel(run.configuration) != "libtv":
        raise Conflict("百炼渠道按阿里云 API 账单计费，不支持 LibTV 积分队列")
    # 已提交上游 = 队列锁定，但「再点一次确认」不该被拦成错误：前端重新提交时会
    # 带上记录里的模式，那不是切换，用户要的只是看进度。只有「把一条已在免费队列
    # 的任务改成积分」才是真冲突，因为它要花钱。
    if run.task_id and mode == "credits" and run.billing_mode != "credits":
        raise Conflict(
            "这条任务已在上游免费队列中，计费队列不能再改；"
            "继续执行只会查询进度。如需用积分生成，请新建一条任务再勾选加速"
        )
    if run.task_id or run.billing_mode == mode:
        return
    run.billing_mode = mode
    # 旧报价属于另一条队列：免费队列报 0、积分队列报正数，沿用它只会误导核对。
    run.estimated_credits = None
    # 免费队列只有在报价非 0 时才会卡在 waiting_free；换到积分队列后应当继续准备，
    # 否则点了加速仍然停在原地，看起来像按钮没反应。
    if mode == "credits" and run.status == "waiting_free":
        run.status = "uploaded"
        run.framework_status = "queued"
        run.last_error = None
    await repo.save(run)
    await session.commit()


async def schedule_state(session, run_id: str, owner: str) -> str:
    """决定这次「继续」该做什么：`done` / `poll` / `prepare`。"""
    repo = RunRepository(session)
    run = await repo.get_or_404(run_id, owner=owner)
    await _restore_bailian_receipt(session, repo, run)
    if run.status == "success":
        return "done"
    if not run.task_id and (run.status == "submission_unknown" or run.submit_attempts > 0):
        raise Conflict(SUBMISSION_UNKNOWN)
    if run.task_id:
        if run.framework_status == "failed" or run.status in ("failed", "service_error"):
            # 人工恢复已有任务，只重开查询预算，不再次提交或扣费。
            begin(run)
            set_status(run, "running")
            run.last_error = None
        run.framework_status = "running"
        await repo.save(run)
        await session.commit()
        return "poll"
    if run.status in ("preparing", "prepare_failed"):
        raise Conflict("输入尚未准备完成，请先使用原配置继续准备")
    if run.status not in RETRYABLE and run.status not in ("queued", "uploading", "uploaded"):
        raise Conflict("该任务当前不能提交")
    # 这里开始计时：上传、排队、生成都算在「这次提交」里
    set_status(run, "queued")
    run.last_error = None
    run.framework_status = "queued"
    # 快照描述的是「上一次提交是怎么发生的」，新一次提交由 mark_submitted 重写；
    # 留着旧的等于让恢复器按上一条记录的分类处置这一次。
    run.submit_mode = None
    # 人工再点「继续」= 重新给一份重试额度：否则上一轮耗尽后，这次刚入队就会在
    # 第一次失败时被判「已重试 5 次」，看起来像按钮没反应。
    run.retry_count = 0
    await repo.save(run)
    await session.commit()
    return "prepare"


async def reconfirm_state(
    session, run_id: str, owner: str, *, confirm_charged: bool
) -> tuple[ReplacementRun, int]:
    """用户确认「上一次可能已经花了钱，我仍要重新提交」后的复位。

    `submission_unknown` 的定义就是「提交动作可能已经成功」，没有出口时这条记录
    只能靠运维进共享盘手改文件。三条路都必须放行：`schedule_state` 会拦、
    `prepare_upload` 会直接 return、`recover_state` 会定格——所以清零
    `submit_attempts` 是唯一的钥匙。复位后仍走 `schedule()`，不在这里直接提交。
    """
    repo = RunRepository(session)
    run = await repo.get_or_404(run_id, owner=owner)
    await _restore_bailian_receipt(session, repo, run)
    if run.task_id:
        # 已有回执：该走「继续」续查询，而不是重提一遍（重提就是第二次计费）
        raise Conflict("该调用已有上游任务号，请直接继续查询结果")
    if run.status != "submission_unknown" and not run.submit_attempts:
        raise Conflict("当前档案不在「提交回执未知」状态，无需确认重提")
    if not confirm_charged:
        raise Conflict(SUBMISSION_UNKNOWN_CONFIRM)
    previous = run.submit_attempts
    run.submit_attempts = 0
    # 确认重提 = 新的一轮计时，上一轮的耗时不再保留
    set_status(run, "queued")
    run.framework_status = "queued"
    run.last_error = None
    run.error_code = None
    run.retry_count = 0
    run.submit_mode = None
    await repo.save(run)
    await session.commit()
    return run, previous


async def schedule(session, run_id: str, owner: str) -> None:
    if not settings.worker_enabled:
        # 配置状态，不是上游故障：503 让前端与运维都不会去查 LibTV
        raise Unavailable("后台执行器未启用（WORKER_ENABLED=false），请联系运维开启")
    action = await schedule_state(session, run_id, owner)
    if action == "done":
        return
    if action == "poll":
        await get_runner().resume(KIND_REPLACEMENT, run_id)
        return
    try:
        await enqueue(run_id)
    except Exception:
        logger.exception("准备任务入队失败 run_id=%s", run_id)
        await mark_schedule_failure(session, run_id)
        raise UpstreamError("准备调度失败，请稍后继续") from None


async def mark_schedule_failure(session, run_id: str) -> None:
    repo = RunRepository(session)
    run = await repo.get(run_id)
    if run is None:
        return
    # 可能已被其他工作进程领取，禁止覆盖其在途状态。
    if run.status == "queued" and not run.task_id and not run.submit_attempts:
        set_status(run, "service_error")
        run.last_error = "准备调度失败，请继续；尚未提交"
        await repo.save(run)
        await session.commit()
    else:
        logger.info("准备任务已由其他执行器接手 run_id=%s", run_id)


async def _bump_retry(session, run_id: str, *, reason: str) -> float | None:
    """记一次自动重试；额度用完返回 None（同时把 run 标成 service_error）。"""
    repo = RunRepository(session)
    run = await repo.get_or_404(run_id)
    run.retry_count += 1
    if run.retry_count > MAX_RETRIES:
        set_status(run, "service_error")
        run.error_code = "RETRY_EXHAUSTED"
        run.last_error = f"{reason}：已自动重试 {MAX_RETRIES} 次仍失败，转人工处理"
        await repo.save(run)
        await session.commit()
        logger.warning("替换任务重试耗尽 run=%s reason=%s", run_id, reason)
        return None
    delay = backoff_delay(run.retry_count)
    await repo.save(run)
    await session.commit()
    return delay


# --------------------------------------------------------------------------- #
# 作业处理器
# --------------------------------------------------------------------------- #
async def _stop_if_unauthorized(session, repo, run: ReplacementRun) -> bool:
    """授权已失效就停住，返回 True 表示「这一轮别继续」。

    与 `ReplacementService.execute()` 排队前那次用的是同一条规则
    （`authorization.authorization_gap`）。两次都要有，而且分工不同：
    排队前那次是给用户即时反馈，这里这次才是真正花钱前的最后一道闸——
    中间隔着 upload 阶段（超时 1500 秒），封装被停用、原片被删都来得及发生。
    旧实现把这道闸放在执行器的 submit 阶段，位置对但只有文件系统；搬到业务层
    之后放回同一时刻，窗口反而比「只在排队前查一次」小。
    """
    gap = await authorization_gap(session, run)
    if gap is None:
        return False
    set_status(run, "service_error")
    # 沿用执行器时代那个码：前端与日志里已经有它，换名只会让历史记录对不上。
    run.error_code = "AUTHORIZATION_REVOKED"
    run.last_error = gap
    await repo.save(run)
    await session.commit()
    logger.warning("提交前授权已失效 run=%s：%s", run.id, gap)
    return True


@register_handler("template.replacement.prepare")
async def prepare_upload(job: Job) -> None:
    from db.session import get_session_factory

    run_id = str(job.payload["id"])
    async with get_session_factory()() as session:
        repo = RunRepository(session)
        run = await repo.get(run_id)
        if run is None:
            logger.info("准备作业放弃：记录已不存在 run_id=%s", run_id)
            return
        await _restore_bailian_receipt(session, repo, run)
        if run.task_id:
            await get_runner().resume(KIND_REPLACEMENT, run_id)
            return
        if run.status in ("submitting", "submission_unknown") or run.submit_attempts:
            return
        # 停用账号不再往回推进：这里之后就是「上传 + 提交」，也就是真正花钱的地方。
        if await owner_disabled(run.owner_id):
            set_status(run, "service_error")
            run.error_code = "OWNER_DISABLED"
            run.last_error = "账号已停用，任务不再继续；恢复账号后可手动继续"
            await repo.save(run)
            await session.commit()
            logger.warning("发起人已停用，替换任务停止准备 run=%s", run_id)
            return

        # upload 会真的往上游传文件、要跑几分钟：授权已经失效就别开始。
        if await _stop_if_unauthorized(session, repo, run):
            return

        path = await asyncio.to_thread(run_path, run_id)
        await asyncio.to_thread(_sync_out, run, path)
        if run_channel(run.configuration) != "libtv":
            from infra.ai.bailian import prepare_inputs

            try:
                await prepare_inputs(run_id, model=CHANNEL_MODELS[run_channel(run.configuration)])
            except Exception:
                logger.exception("百炼素材上传失败 run_id=%s", run_id)
                set_status(run, "service_error")
                run.error_code = "BAILIAN_UPLOAD_FAILED"
                run.last_error = "百炼素材上传或校验未完成，尚未提交生成；请核对素材后继续"
                await repo.save(run)
                await session.commit()
                return
            run.status = "uploaded"
            run.retry_count = 0
            if await _stop_if_unauthorized(session, repo, run):
                return
            run.framework_status = "queued"
            await repo.save(run)
            await session.commit()
            await get_runner().dispatch(KIND_REPLACEMENT, run_id)
            return
        result = await execute_stage(run_id, "upload")

        run = await repo.get_or_404(run_id)
        await asyncio.to_thread(_absorb, run, path)
        if result.code == "EXECUTOR_BUSY":
            run.status = "waiting_executor"
            run.last_error = result.message
            await repo.save(run)
            await session.commit()
            delay = await _bump_retry(session, run_id, reason="执行器持续被占用")
            if delay is None:
                return
            await enqueue(run_id, delay=delay)
            return
        if result.code != "OK" or run.status not in ("uploaded", "waiting_free"):
            before = run.status
            set_status(run, "service_error")
            # `result.code` 的默认值就是 "OK"，直接拿来当错误码会让页面显示
            # 「上传准备未完成：OK」。只有真出错才带上游码。
            run.error_code = result.code if result.code != "OK" else "UPLOAD_INCOMPLETE"
            run.last_error = (
                f"上传准备未完成：{result.message or result.code}（档案状态 {before}）"
            )
            await repo.save(run)
            await session.commit()
            return
        run.retry_count = 0
        if run.status == "waiting_free":
            await repo.save(run)
            await session.commit()
            return
        # 上传期间封装可能被停用、原片可能被删——真正提交前的最后一次。
        if await _stop_if_unauthorized(session, repo, run):
            return
        run.framework_status = "queued"
        await repo.save(run)
        await session.commit()
        try:
            await get_runner().dispatch(KIND_REPLACEMENT, run_id)
        except Exception:
            logger.exception("生成调度失败 run_id=%s", run_id)
            run = await repo.get_or_404(run_id)
            set_status(run, "service_error")
            run.last_error = "生成调度失败，请继续；尚未提交"
            await repo.save(run)
            await session.commit()
            raise UpstreamError("生成调度失败，请继续") from None


@register_handler("libtv.download")
async def download(job: Job) -> None:
    from db.session import get_session_factory

    run_id = str(job.payload["id"])
    result = await execute_stage(run_id, "download")
    if result.code == "OK":
        await publish_output(run_id)
        # 结算金额只在提交回执里可靠，下载完成后回填一次（不重试、不改上游）。
        await sync_credit_charge(run_id)
        return
    logger.warning("成片下载待恢复 run_id=%s code=%s", run_id, result.code)
    async with get_session_factory()() as session:
        repo = RunRepository(session)
        run = await repo.get(run_id)
        if run is None:
            # 记录已被清掉：继续排下去只会每 30 秒报一次同样的错。
            logger.info("成片下载放弃：调用记录已不存在 run_id=%s", run_id)
            return
        delay = await _bump_retry(session, run_id, reason="成片下载失败")
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


async def publish_output(run_id: str) -> None:
    """把工作区里的成片搬进归档存储，并在 `output_key` 上登记。

    **为什么必须搬**：`output_key` 为空时，任务列表与详情页的 `outputUrl` 也是空
    （前端靠它决定「播不播」），所以成片只存在工作区时用户看不到结果——这是
    「成片在本机磁盘上」这条约束的直接后果，不是另一个功能。

    搬失败不算任务失败：本地文件还在，`output()` 有本地兜底；这里只记一条警告，
    等下一次下载重试或人工处置。把「上传失败」判成「生成失败」会让一条已经花钱
    做出来的片子被标成失败。
    """
    from db.session import get_session_factory

    folder = run_path(run_id) / "outputs/generated.mp4"
    if not folder.is_file():
        logger.warning("成片不存在，跳过归档 run_id=%s", run_id)
        return
    key = f"runs/{run_id}/outputs/generated.mp4"
    try:
        await asyncio.to_thread(get_archive_storage().copy, folder, key)
    except Exception:  # noqa: BLE001 - 上传失败要降级，不能让任务被判失败
        logger.exception("成片归档失败，暂时只能从工作区读取 run_id=%s", run_id)
        return
    async with get_session_factory()() as session:
        repo = RunRepository(session)
        run = await repo.get(run_id)
        if run is None:
            return
        run.output_key = key
        await repo.save(run)
        await session.commit()


async def enqueue_recovery(run_id: str, *, delay: float = 0, attempt: int = 0) -> None:
    """投一次恢复作业。`attempt` 随 payload 传给下一轮。

    计数只能这样带：重投是新建作业（`max_attempts=1`），作业自身的 `attempts`
    每次都是 0，拿它当轮次会永远看到第 1 轮。
    """
    await get_queue().enqueue(
        Job(
            name="template.replacement.recover",
            payload={"id": run_id, "attempt": attempt},
            max_attempts=1,
            idempotency_key=f"replacement:recover:{run_id}",
        ),
        delay=delay,
    )


async def recover_state(session, run_id: str) -> str:
    repo = RunRepository(session)
    run = await repo.get(run_id)
    if run is None:
        return "done"
    await _restore_bailian_receipt(session, repo, run)
    if run.task_id and run.status != "success":
        run.framework_status = "running"
        await repo.save(run)
        await session.commit()
        return "poll"
    if run.status == "submitting" or (not run.task_id and run.submit_attempts):
        set_status(run, "submission_unknown")
        run.framework_status = "failed"
        run.last_error = "提交中断，请先核对上游回执；禁止自动重提"
        await repo.save(run)
        await session.commit()
    elif run.status in ("queued", "uploading", "uploaded", "waiting_executor", "prepared"):
        return "prepare"
    return "done"


async def _restore_bailian_receipt(session, repo, run: ReplacementRun) -> None:
    """Reattach an accepted upstream task if the process died before DB acknowledgement."""
    if run.task_id or run_channel(run.configuration) == "libtv":
        return
    try:
        path = await asyncio.to_thread(run_path, run.id)
        raw = await asyncio.to_thread(
            (path / "records/bailian-submission.json").read_text, encoding="utf-8"
        )
        receipt = json.loads(raw)
    except (OSError, ValueError, NotFound):
        return
    task_id = receipt.get("task_id") or (receipt.get("output") or {}).get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return
    run.task_id = task_id
    run.submit_attempts = 0
    run.submit_mode = "async"
    run.framework_status = "running"
    if run.status in SETTLED_STATUSES:
        begin(run)
    set_status(run, "running")
    run.last_error = None
    await repo.save(run)
    await session.commit()


def archive_bailian_unknown_receipt(run_id: str) -> None:
    """Only call after explicit paid-retry confirmation has been audited."""
    path = run_path(run_id)
    with lease(path / "records/bailian-submit.lock"):
        receipt = path / "records/bailian-submission.json"
        if not receipt.is_file():
            return
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        if payload.get("task_id") or (payload.get("output") or {}).get("task_id"):
            raise Conflict("百炼已有上游任务号，请继续查询原任务，不能重新提交")
        receipt.rename(path / f"records/bailian-reconfirmed-{uuid4().hex}.json")


@register_handler("template.replacement.recover")
async def recover(job: Job) -> None:
    from db.session import get_session_factory

    run_id = str(job.payload["id"])
    async with get_session_factory()() as session:
        try:
            action = await recover_state(session, run_id)
        except Conflict:
            # 另一台实例正在提交：只等回执，绝不覆盖它的状态或重提。
            await _wait_for_release(run_id, int(job.payload.get("attempt") or 0))
            return
    if action == "poll":
        await get_runner().resume(KIND_REPLACEMENT, run_id)
    elif action == "prepare":
        await enqueue(run_id)


async def _wait_for_release(run_id: str, attempt: int) -> None:
    """恢复作业等不到状态时的退避重投，轮次用完就放手。

    放弃时**什么都不写**：真正持锁/持事务的那个进程收尾时会自己把状态写正确；
    它若崩了，下一轮恢复就能正常继续。写一笔「猜测的状态」才是覆盖回执的起点。
    """
    if attempt >= RECOVER_LOCK_MAX_RETRIES:
        logger.error(
            "替换任务恢复放弃 run=%s：已等待 %d 轮。记录状态未被改动，"
            "请检查是否仍有监督进程在提交",
            run_id,
            attempt,
        )
        RECOVERY_LOCK_GIVEUPS.inc()
        return
    delay = min(RECOVER_LOCK_BASE_SEC * 2**attempt, RECOVER_LOCK_MAX_SEC)
    logger.info("替换任务恢复退避 %.0fs run=%s 第 %d 轮", delay, run_id, attempt + 1)
    await enqueue_recovery(run_id, delay=delay, attempt=attempt + 1)


async def resume() -> None:
    """进程启动恢复：只挑「还有事可做」的记录进队列。

    上一版用 `meta.task_id` 做条件，命中**所有**历史成功记录——每次启动都白投
    一次、白读两次盘；更糟的是本地已判失败的记录会被翻回 running 并真的再查一次
    上游，集合永不收敛。终态与「等人确认」的状态一律跳过，收敛才成立。

    **不按 `APP_ENV` 跳过**：要隔离数据就指向测试库，而不是让环境变量顺手关掉
    整段逻辑——被关掉的代码测试永远发现不了它的 bug。
    """
    from db.session import get_session_factory

    if not settings.worker_enabled:
        logger.debug("worker 未启用，跳过替换任务恢复扫描")
        return
    try:
        async with get_session_factory()() as session:
            repo = RunRepository(session)
            rows = await repo.list_unfinished(
                list((UNFINISHED_STATUSES | SETTLED_STATUSES) - {"success"})
            )
            ids = []
            for row in rows:
                # Only a durable accepted Bailian receipt may reopen a settled DB row.
                # Unknown receipts remain settled and never create another paid task.
                if row.status in SETTLED_STATUSES:
                    if row.task_id or row.status == "success":
                        continue
                    await _restore_bailian_receipt(session, repo, row)
                    if not row.task_id:
                        continue
                ids.append(row.id)
    except (UpstreamError, OSError):
        logger.warning("数据库暂不可用，替换任务等待恢复")
        return
    for run_id in ids:
        await enqueue_recovery(run_id)
    if ids:
        logger.info("替换任务恢复：%d 条待推进", len(ids))
