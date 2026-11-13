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
from contextlib import nullcontext
from uuid import uuid4

from sqlalchemy.orm.exc import StaleDataError

from core.config import settings
from core.exceptions import Conflict, NotFound, Unavailable, UpstreamError
from core.logging import get_logger
from core.metrics import METRICS
from core.ports.accounts import account_is_active
from generation.runner import get_runner
from infra import get_queue
from infra.ai.libtv import execute_stage
from infra.ai.libtv_state import RUN_LOCK
from infra.archive import get_archive_storage
from infra.archive.base import lease, read_json
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
        "preparing", "submitted", "running", "generated",
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

# 归档与投影都是「失败也不报错」的降级路径：前者让任务显示成功却播不了，
# 后者让执行器读到旧状态。没有计数时这两类问题只能靠翻日志。
ARCHIVE_PUBLISH_TOTAL = METRICS.counter(
    "replacement_archive_publish_total",
    "成片归档到对象存储的结果分布",
    labels=("result",),
)
PROJECTION_SKIPPED = METRICS.counter(
    "replacement_projection_skipped_total",
    "状态投影（run.json）写不进去而本次跳过的次数",
    labels=("reason",),
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


def _sync_out(run: ReplacementRun, path) -> bool:
    """Nonblocking projection under the exact lease held by the supervisor.

    Never wait with an old DB snapshot, or erase an executor's accepted receipt.
    The context manager stays entirely in this thread (Windows byte-range locks).

    返回 `False` 一律表示「这一份投影暂时写不进去，下次再补」：调用方据此退避，
    而不是当成任务失败。所以除了「锁被占」（`Conflict`），还要接住锁文件本身
    建不出来的 `OSError`（共享盘掉了、路径被文件占住、只读挂载）——
    让它冒泡会把一条还没提交的任务记成 `service_error`，那是**投影**的问题，
    不是**任务**的问题。
    """
    try:
        with lease(path / RUN_LOCK):
            state = read_state(path)
            file_version = state.get("state_version")
            if isinstance(file_version, int) and file_version > getattr(run, "state_version", 1):
                return False
            if state.get("id") not in (None, run.id):
                return False
            local_task = state.get("task_id") or state.get("taskId") or _libtv_task(
                path, run.id, state
            )
            if local_task and local_task != run.task_id:
                return False
            if state.get("status") in ("generated", "success") and run.status not in (
                state["status"], "success"
            ):
                return False
            if not run.task_id and state.get("status") in (
                "submitting", "submission_unknown", "submitted", "running", "generated", "success"
            ) and state.get("status") != run.status:
                return False
            _write_projection(run, path)
            return True
    except Conflict:
        # 上面那些 `return False` 是「本轮不需要写」，不是「写不进去」；
        # 只有这两条异常分支才是真正的降级，计数也只加在这里。
        PROJECTION_SKIPPED.inc(reason="lock")
        return False
    except OSError:
        logger.warning("状态投影暂时写不进去（工作区不可达），本次跳过 run=%s", run.id)
        PROJECTION_SKIPPED.inc(reason="io")
        return False


def _write_projection(run: ReplacementRun, path) -> None:
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
            "state_version": getattr(run, "state_version", 1),
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


def sync_projection(run: ReplacementRun) -> bool:
    """把数据库行投影进工作区（`run.json`）。

    公开这一个入口给「准备」用：准备阶段也产生新状态（`prepared`），执行器接手
    之前必须能看到。业务层只写不读，读回来仍走 `_absorb`。
    """
    return _sync_out(run, run_path(run.id))


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
    file_version = state.get("state_version")
    if isinstance(file_version, int) and file_version < getattr(run, "state_version", 1):
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
        # 回执是执行器**边跑边写**的：`read_json` 的有限重试要能跨过共享盘的替换
        # 窗口，否则轮询期间会偶尔读到「没有回执」，账单就一直显示待核对。
        payload = read_json(run_path(run_id) / "records/submission.json")
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    value = (payload.get("data") or {}).get("power")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


async def sync_credit_charge(run_id: str) -> None:
    """下载完成后把真实扣费写回数据库，供任务中心/档案列表展示。"""
    charged = await asyncio.to_thread(receipt_power, run_id)
    if charged is None:
        return
    await _update_column(run_id, lambda run: setattr(run, "credits", charged))


async def _update_column(run_id: str, apply) -> bool:
    """只补一列（输出键、真实扣费）的写入：被并发写挤掉就重读一次再写。

    这两列与业务状态无关，重试天然幂等。它们又恰好都发生在「轮询正在推进度」
    的时刻，而 `state_version` 会让输的一方抛 `StaleDataError`——不重试的后果是
    下载作业失败进死信：片子做出来了但没有归档地址，或者账单永远显示「待核对」。
    重试一次仍撞车才放手（日志留痕），不让一次补写把整条链路拖垮。
    """
    from db.session import get_session_factory

    for _ in range(2):
        async with get_session_factory()() as session:
            run = await RunRepository(session).get(run_id)
            if run is None:
                return False
            apply(run)
            try:
                await session.commit()
                return True
            except StaleDataError:
                await session.rollback()
    logger.warning("补写列持续撞车（并发写太频繁），本次跳过 run=%s", run_id)
    return False


async def enqueue(run_id: str, *, delay: float = 0, attempt: int = 0) -> None:
    await get_queue().enqueue(
        Job(
            name="template.replacement.prepare",
            # `attempt` 只服务「准备阶段的退避轮次」：恢复链把轮次带下来，
            # 否则 `_wait_for_release` 的封顶在 prepare 这一跳会归零、退化成无限循环。
            payload={"id": run_id, "attempt": attempt},
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
    try:
        path = await asyncio.to_thread(run_path, run_id)
    except NotFound:
        # Legacy DB-only rows have no executor workspace to race with.
        path = None
    # Enter and exit on the event-loop thread. Acquisition is nonblocking;
    # keep the supervisor excluded through receipt recovery and the DB commit.
    with lease(path / RUN_LOCK) if path is not None else nullcontext():
        await session.refresh(run)
        if not run.task_id and path is not None:
            task_id = await asyncio.to_thread(_libtv_task, path, run.id, read_state(path))
            if task_id:
                await _attach_accepted_task(session, repo, run, task_id)
        await _change_billing_mode(session, repo, run, mode)


async def _change_billing_mode(session, repo, run: ReplacementRun, mode: str) -> None:
    # The submit handler may already have frozen a request before its supervisor
    # acquires the executor lease. That intent also locks the billing queue.
    if run.submit_attempts > 0 and mode != run.billing_mode:
        raise Conflict("该任务已进入提交阶段，计费模式不能再改；请先核对原提交结果")
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
    await _restore_accepted_receipt(session, repo, run)
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
    await _restore_accepted_receipt(session, repo, run)
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
    """记一次自动重试；额度用完返回 None（同时把 run 标成 service_error）。

    **计数写不进去不能让链路断**：这条路径与轮询在改同一行（下载失败的同时，
    另一次查询正在写进度），`state_version` 会把输的一方顶成 `StaleDataError`。
    重试计数只是「防无限重试」的保险，丢一次增长最多多跑一轮；而异常冒到队列
    会让作业进死信——成片下不来、也没有任何后续作业。所以撞车重读一次，
    仍撞车就按「这一次没记上」继续，绝不因此判任务失败。
    """
    repo = RunRepository(session)
    delay: float | None = None
    for attempt in range(2):
        try:
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
        except StaleDataError:
            await session.rollback()
            if attempt:
                logger.warning(
                    "重试计数写不进去（并发写入过密），本轮按未计数继续 run=%s reason=%s",
                    run_id,
                    reason,
                )
    return delay or backoff_delay(1)


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


async def _prepare_channel_inputs(run_id: str, model: str) -> None:
    """渠道要求「先上传素材」时，走供应商的可选协议。

    替换链不认识具体供应商：能不能上传、密钥配没配，都由驱动自己回答
    （`infra.ai.base.InputPreparer`）。这样接一个新渠道不必回来改这里，
    也不会出现 `modules/template` 直接 import 某个驱动文件的横向依赖。
    """
    from infra import get_model_router
    from infra.ai.base import InputPreparer

    provider = get_model_router().provider_for(model, allow_fallback=False)
    if not isinstance(provider, InputPreparer):
        raise Unavailable(f"渠道 {model} 不支持素材上传，无法提交")
    await provider.prepare_inputs(run_id, model=model)


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
        try:
            await _restore_accepted_receipt(session, repo, run)
        except Conflict:
            await _wait_for_release(run_id, int(job.payload.get("attempt") or 0))
            return
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

        # Prepared inputs still await the user's explicit execution request.
        if run.status == "prepared":
            return

        # upload 会真的往上游传文件、要跑几分钟：授权已经失效就别开始。
        if await _stop_if_unauthorized(session, repo, run):
            return

        path = await asyncio.to_thread(run_path, run_id)
        if not await asyncio.to_thread(_sync_out, run, path):
            # 投影写不进去时走**有上限**的退避：以前这里固定 delay、attempt 恒 0，
            # 一旦 `_sync_out` 是永久性 False（文件里的版本比库里新、task_id 不一致、
            # 旧备份回灌），就是一条 30s 一轮、永不收敛的循环，且无告警。
            await _wait_for_release(
                run_id, int(job.payload.get("attempt") or 0), reason="投影写不进去"
            )
            return
        if run_channel(run.configuration) != "libtv":
            try:
                await _prepare_channel_inputs(
                    run_id, CHANNEL_MODELS[run_channel(run.configuration)]
                )
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
        if result.code == "EXECUTOR_LOGIN_REQUIRED":
            # 「浏览器里没登录」是**可恢复**状态，不是失败：UI 对 `needs_login` 有专门
            # 的下一步提示（去专用浏览器登录后重试），`RETRYABLE` 也允许继续。
            # 归到 service_error 会让用户以为任务坏了，而实际只需要登一次。
            set_status(run, "needs_login")
            run.error_code = result.code
            run.last_error = result.message
            await repo.save(run)
            await session.commit()
            logger.warning("执行器浏览器未登录，等待人工登录后继续 run=%s", run_id)
            return
        if result.code != "OK" or run.status not in ("uploaded", "waiting_free"):
            before = run.status
            set_status(run, "service_error")
            # `result.code` 的默认值就是 "OK"，直接拿来当错误码会让页面显示
            # 「上传准备未完成：OK」。只有真出错才带上游码。
            run.error_code = result.code if result.code != "OK" else "UPLOAD_INCOMPLETE"
            # 已经说清「是什么、该做什么」的码原样给出：再套一层
            # 「上传准备未完成：…（档案状态 queued）」只会把可执行的信息淹掉。
            run.last_error = (
                result.message
                if result.code == "EXECUTOR_BROWSER_UNAVAILABLE"
                else f"上传准备未完成：{result.message or result.code}（档案状态 {before}）"
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
    folder = run_path(run_id) / "outputs/generated.mp4"
    if not folder.is_file():
        logger.warning("成片不存在，跳过归档 run_id=%s", run_id)
        ARCHIVE_PUBLISH_TOTAL.inc(result="missing")
        return
    key = f"runs/{run_id}/outputs/generated.mp4"
    try:
        await asyncio.to_thread(get_archive_storage().copy, folder, key)
    except Exception:  # noqa: BLE001 - 上传失败要降级，不能让任务被判失败
        logger.exception("成片归档失败，暂时只能从工作区读取 run_id=%s", run_id)
        ARCHIVE_PUBLISH_TOTAL.inc(result="failed")
        return
    if not await _update_column(run_id, lambda run: setattr(run, "output_key", key)):
        logger.warning("成片已归档但输出键没落库，下次下载重试会补上 run_id=%s", run_id)
        # 字节进了桶、行上的键没写：任务显示成功但播不了，和上传失败一样要告警。
        ARCHIVE_PUBLISH_TOTAL.inc(result="unrecorded")
        return
    ARCHIVE_PUBLISH_TOTAL.inc(result="ok")


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
    await _restore_accepted_receipt(session, repo, run)
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
    elif run.status in ("queued", "uploading", "uploaded", "waiting_executor"):
        return "prepare"
    return "done"


def _libtv_task(path, run_id: str, state: dict) -> str | None:
    """A receipt belongs to this run only when its workspace identity agrees."""
    if state.get("id") != run_id:
        return None
    task = state.get("task_id") or state.get("taskId")
    if isinstance(task, str) and task:
        return task
    try:
        receipt = read_json(path / "records/submission.json")
    except (OSError, ValueError):
        return None
    if not isinstance(receipt, dict) or receipt.get("id", run_id) != run_id:
        return None
    data = receipt.get("data")
    if not isinstance(data, dict):
        return None
    task = data.get("taskId") or data.get("task_id")
    return task if isinstance(task, str) and task else None


def _accepted_libtv_task(run_id: str) -> str | None:
    try:
        path = run_path(run_id)
    except NotFound:
        return None
    with lease(path / RUN_LOCK):
        return _libtv_task(path, run_id, read_state(path))


async def recover_submission(session, run_id: str, owner: str) -> ReplacementRun:
    """Restore accepted work before authorization of a possible new submission."""
    repo = RunRepository(session)
    run = await repo.get_or_404(run_id, owner=owner)
    await _restore_accepted_receipt(session, repo, run)
    return run


async def _restore_accepted_receipt(session, repo, run: ReplacementRun) -> None:
    if run.task_id:
        return
    if run_channel(run.configuration) != "libtv":
        await _restore_bailian_receipt(session, repo, run)
        return
    task_id = await asyncio.to_thread(_accepted_libtv_task, run.id)
    if not task_id:
        return
    await _attach_accepted_task(session, repo, run, task_id)


async def _attach_accepted_task(session, repo, run: ReplacementRun, task_id: str) -> None:
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


async def _restore_bailian_receipt(session, repo, run: ReplacementRun) -> None:
    """Reattach an accepted upstream task if the process died before DB acknowledgement."""
    if run.task_id or run_channel(run.configuration) == "libtv":
        return
    try:
        path = await asyncio.to_thread(run_path, run.id)
        receipt = await asyncio.to_thread(
            read_json, path / "records/bailian-submission.json"
        )
    except (OSError, ValueError, NotFound):
        return
    if not isinstance(receipt, dict):
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
        payload = read_json(receipt)
        if not isinstance(payload, dict):
            # 读不出来就当**不能确认**：这条路径是「已花钱的任务要不要重提」，
            # 把「读不到回执」当成「没有任务号」会直接导致重复计费。
            raise Conflict("提交回执无法解读，请稍后重试；在此之前不要重复提交")
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
        await enqueue(run_id, attempt=int(job.payload.get("attempt") or 0))


async def _wait_for_release(run_id: str, attempt: int, *, reason: str = "另一实例仍在提交") -> None:
    """恢复作业等不到状态时的退避重投，轮次用完就放手。

    放弃时**什么都不写**：真正持锁/持事务的那个进程收尾时会自己把状态写正确；
    它若崩了，下一轮恢复就能正常继续。写一笔「猜测的状态」才是覆盖回执的起点。

    `reason` 只用于最后的日志：同样是「等不到」，等锁、等投影、等另一个实例收尾
    的排查方向完全不同，别让一句话把值班引到错的地方。
    """
    if attempt >= RECOVER_LOCK_MAX_RETRIES:
        logger.error(
            "替换任务恢复放弃 run=%s：已等待 %d 轮（%s）。记录状态未被改动，请人工确认这条任务",
            run_id,
            attempt,
            reason,
        )
        RECOVERY_LOCK_GIVEUPS.inc()
        return
    delay = min(RECOVER_LOCK_BASE_SEC * 2**attempt, RECOVER_LOCK_MAX_SEC)
    logger.info("替换任务恢复退避 %.0fs run=%s 第 %d 轮（%s）", delay, run_id, attempt + 1, reason)
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
            # 先取成纯数据：某一行的回执恢复失败时要 rollback 才能继续，而 rollback
            # 会让本 session 里的 ORM 实例全部过期，之后再读 `row.status` 就是异步
            # 上下文里的隐式 IO（MissingGreenlet）——跳过这一行的动作本身又会炸。
            cases = [(row.id, row.status, row.task_id) for row in rows]
            ids = []
            for run_id, status, task_id in cases:
                # Only a durable accepted Bailian receipt may reopen a settled DB row.
                # Unknown receipts remain settled and never create another paid task.
                if status in SETTLED_STATUSES:
                    if task_id or status == "success":
                        continue
                    try:
                        row = await repo.get(run_id)
                        if row is None:
                            continue
                        await _restore_accepted_receipt(session, repo, row)
                        restored = bool(row.task_id)
                    except Conflict:
                        # Recovery jobs already have bounded lease backoff; startup
                        # must neither fail nor guess a state for the active executor.
                        ids.append(run_id)
                        continue
                    except Exception:  # noqa: BLE001
                        # 一条读不懂的历史行不能拦住整个启动：`run_path` 在共享盘
                        # 掉线时抛 OSError、旧格式记录可能缺字段、乐观锁可能撞车。
                        await session.rollback()
                        logger.exception("启动恢复跳过一条无法解读的记录 run=%s", run_id)
                        continue
                    if not restored:
                        continue
                ids.append(run_id)
    except (UpstreamError, OSError):
        logger.warning("数据库暂不可用，替换任务等待恢复")
        return
    for run_id in ids:
        await enqueue_recovery(run_id)
    if ids:
        logger.info("替换任务恢复：%d 条待推进", len(ids))
