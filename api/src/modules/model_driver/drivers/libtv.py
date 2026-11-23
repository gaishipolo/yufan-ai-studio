"""LibTV 驱动：视频模型（自动载入，不写 `AI_PROVIDERS`）。

整体迁移后本模块同时拥有模型声明、Provider **和**执行器：
`modules/model_driver/libtv/` 里是监督进程、状态模型与固定版 Node 适配器，
子进程以 `-m modules.model_driver.libtv.process` 启动，`runtime/vendor`
按模块自身位置定位。

调用方（`modules/task` 的通用链）不许直接 import 业务模块，所以它经
`core/ports/model_generation` 拿 `execute_stage` / `VENDOR` / `RUN_LOCK`；
本文件是唯一直接调执行器的地方。
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Mapping
from pathlib import Path

from core.exceptions import BadRequest, Conflict, RateLimited, UpstreamError
from core.logging import get_logger
from infra import get_queue, get_storage
from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
    ModelSpec,
    ReferenceLimit,
)
from infra.queue.base import Job
from modules.model_driver.generic_inputs import materialize
from modules.model_driver.libtv import account as libtv_account
from modules.model_driver.libtv import executor, process
from modules.model_driver.libtv.models import (
    DEFAULT_LIBTV_MODEL_ID,
    LIBTV_PROFILES,
    LibTVModelProfile,
    resolve_libtv_profile,
)
from modules.model_driver.libtv.reconcile import reconcile as reconcile_state
from modules.model_driver.libtv.state import LibTVState
from modules.model_driver.spec import DriverMeta

META = DriverMeta(
    label="LibTV",
    description="Wan 3.0 / Seedance 2.x 视频生成与替换（归档输入 + 免费/积分队列）",
    version="1.1.0",
    author="",
)

MODEL_ID = DEFAULT_LIBTV_MODEL_ID

logger = get_logger(__name__)

#: 去字幕模型（上游 `volcano-subtitle-eraser`）：源视频来自父任务，不新建档案
DESUBTITLE_MODEL = "libtv-subtitle-eraser"

# 这些故障需要修复执行环境，再由「继续」恢复已有 task_id 的查询。
EXECUTOR_BLOCKED = frozenset(
    {
        "STALE_LOCK",
        "LOCK_CLEANUP_FAILED",
        "EXECUTOR_IO_FAILED",
        "EXECUTOR_CRASHED",
        "EXECUTOR_ENVIRONMENT_BLOCKED",
        # 发布物与清单不一致：这是「这台机器上的执行器不是这一版代码」，
        # 修法是刷新清单或停掉那个多余的 checkout，不是重试。
        "EXECUTOR_RELEASE_MISMATCH",
    }
)

def _param_schema(profile: LibTVModelProfile) -> dict[str, object]:
    """按模型能力声明参数 schema（前端据此动态渲染，不写死字段）。"""
    return {
        # 键名与 `modules/task/params.py` 对齐：前端按 paramSchema 渲染，不加代码。
        "durationSec": {
            "type": "int",
            "min": profile.min_duration,
            "max": profile.max_duration,
            "step": 1,
            "default": 5,
        },
        "aspectRatio": {
            "type": "enum",
            # 与 `core.enums.AspectRatio` 对齐（`CreateTaskIn.aspectRatio` 强校验这三档）。
            "options": ["9:16", "16:9", "1:1"],
            "default": "9:16",
        },
        "resolution": {
            "type": "enum",
            "options": list(profile.resolutions),
            "default": profile.default_resolution,
            "label": "分辨率",
        },
        # 计费模式。免费队列（free）走 `params.infiniteSwitch = 0`；积分加速
        # （credits）走 `infiniteSwitch = 1` 并在请求体里关掉账号当前的 0 积分
        # 权益标签（顶层与 params 各写一份 `disableBenefitTags`）。
        #
        # 默认 `free` 是保守的那一侧：免费队列慢一点，但不误扣积分。
        #
        # `userSelectable`：这个参数**由使用模板的人选**，不该由模板作者写死。
        # 写死 = 模板作者替每个使用者决定了要不要花积分 —— 而积分是**用的人**的钱。
        # 标记后：封装页不许设它（设了就报错），模板库填表时渲染成选择控件。
        "billingMode": {
            "type": "enum",
            "options": ["free", "credits"],
            "default": "free",
            "label": "计费模式",
            "userSelectable": True,
            "description": (
                "免费队列（较慢，不扣积分）/ 积分加速（更快，消耗账号积分）。"
                "积分加速要求账号当前有生效的 0 积分权益标签，否则不会提交"
            ),
        },
    }


MODELS: tuple[ModelSpec, ...] = (
    *(
        ModelSpec(
            id=profile.id,
            label=profile.label,
            provider="libtv",
            capability=Capability.VIDEO,
            description=profile.description,
            param_schema=_param_schema(profile),
            max_references=profile.max_references,
            # LibTV 的混合输入通道吃图片、视频与音频：图片是身份 / 商品 / 场景，
            # 视频是原片或动作参考，音频是音轨 / 口型。三类共用同一条 `mixedList`。
            reference_kinds=("image", "video", "audio"),
            # 替换链的模型：有原片 + 素材包的档案工作区，能按模板做替换。
            replacement=True,
            aliases=profile.aliases,
        )
        for profile in LIBTV_PROFILES
    ),
    ModelSpec(
        id=DESUBTITLE_MODEL,
        label="LibTV · 智能去字幕",
        provider="libtv",
        capability=Capability.VIDEO,
        description="AI 一键去除视频里的字幕（仅中英文），输入是另一条任务的成片",
        # 不给用户任何参数：源视频、宽高、时长都由父任务决定。
        # 暴露出来只会让人以为可以随便传一个 URL 进来。
        param_schema={},
        # 不进货架：它只能由「任务卡 → 智能去字幕」调用（源视频必须是某条任务的成片），
        # 出现在创建页下拉里必然缺参。
        internal=True,
    ),
)


def _request_ready(path: Path) -> bool:
    """`inputs/request.json` 是 upload 阶段的产物，submit 阶段必须读它。"""
    return (path / "inputs/request.json").is_file()


def _uncertain_hint(run_id: str, account: str) -> str:
    """「回执未知」时用户真正能做的事 —— 不是再点一次重试。

    文案要同时给全三样：为什么不能自动重试（可能已扣费）、去哪核对（哪个账号）、
    核对完的两个出口（解封 / 接回）。此前这里只有一句「请核对后手动重试」，
    而重试必然再撞上同一道闸门 —— 用户点十次也是同一句。
    """
    return (
        f"上游提交回执未知（账号 {account}）：请求已发出但没拿到 task_id，"
        "可能已经扣费，所以不会自动重提。请到该账号的 LibTV 记录核对后再二选一："
        f"没有这笔任务 → app libtv-reconcile {run_id} --not-submitted 解封后重试；"
        f"已有任务在跑 → app libtv-reconcile {run_id} --adopt-task <上游 task_id> 接回查询；"
        f"查结算账单自动接回 → app libtv-reconcile {run_id} --auto"
    )


async def _resolve_unknown(
    path: Path, previous: object, account: str
) -> tuple[str, str]:
    """「回执未知」时查一次消费明细：若已结算命中，直接接回查询，不再重提。

    消费流水只在成片结算后入账，排队/生成中的在途任务不会出现——因此未命中时
    一律回 `("unknown", "")` 交回人工核对，绝不自动解封重提（避免重复排队/扣费）。
    """
    from modules.model_driver.libtv import account as libtv_account

    created = getattr(previous, "created_at", None)
    try:
        verdict = await libtv_account.auto_reconcile_verdict(path, created, account)
    except UpstreamError as exc:
        logger.warning("回执未知，自动查询消费明细失败：%s", exc)
        return ("unknown", "")
    if verdict.action == "adopt":
        logger.info("回执未知，但消费明细命中上游任务 id=%s", verdict.task_id)
        return ("adopt", verdict.task_id)
    logger.warning("回执未知且无法自动判断（%s）", verdict.describe())
    return ("unknown", "")


def _waiting_free_error(state: LibTVState) -> BadRequest:
    """默认免费队列拿不到 0 积分报价时，给足价钱与「改用积分队列」出口。"""
    cost = state.estimated_credits
    price = f"{cost:g} 积分" if isinstance(cost, (int, float)) else "非零"
    return BadRequest(
        f"当前没有 0 积分畅用权益，免费队列报价 {price}，未提交；"
        "改用积分队列（billingMode=credits）或等权益恢复后再提交"
    )


def _failed_message(meta: LibTVState, live_reason: str = "") -> str:
    """把上游失败原因原样透出到工作台，不再只回一句「请查看调用档案」。"""
    reason = (meta.failed_reason or meta.last_error or live_reason).strip()
    return f"LibTV 生成失败：{reason}" if reason else "LibTV 生成失败，请查看调用档案"


async def _poll_live(task_id: str, account: str):  # noqa: ANN202 - 轻量 HTTP 轮询
    """进行中走纯 Python HTTP 查进度，避免每次轮询起 Node 子进程并全盘算 SHA256。"""
    from modules.model_driver.libtv import account as libtv_account

    try:
        items = await libtv_account.progress([task_id], account)
    except UpstreamError:
        return None
    return next((item for item in items if item.task_id == task_id), None)


def _stage_failure(result: executor.ExecutorResult, run_id: str, account: str) -> Exception:
    """把执行器回执翻译成「下一步该做什么」，而不是一句笼统的失败。

    `EXECUTOR_BUSY` 是**排队**信号：同一个账号同一时刻只允许一个任务上传/提交，
    而上传素材本身可能占几分钟。抛 `RateLimited` 让框架按
    `GENERATION_THROTTLE_DELAYS` 把它重新排队（任务保持 queued），
    而不是判死 —— 否则用户连点三个任务，第二个就得到「提交生成失败」。

    `STALE_LOCK` 是上一轮留下的锁，要人核对档案，不能自动重来；
    发布物不一致（`EXECUTOR_RELEASE_MISMATCH`）同理，重试只会重复同一条错。
    """
    if result.code == "EXECUTOR_BUSY":
        return RateLimited(f"账号 {account} 正在执行其他任务，已转入排队，稍后自动重试")
    return BadRequest(f"{result.message or result.code}；未提交生成")


class LibTVProvider:
    name = "libtv"
    capabilities = frozenset({Capability.VIDEO})
    # 返回任务 ID、由框架续轮询；恢复器按缺省 False 处理，必须显式声明
    supports_poll = True
    # 轻量 HTTP 轮询的基准节奏：5 秒一跳；排队中由 runner 自动退避到 15 秒。
    poll_interval_sec = 5.0
    models = MODELS
    # 通用入口要在本跳里先跑 `upload` 再跑 `submit`（模板库那条链把 upload
    # 排成独立的 prepare job，这里没有）。监督进程给的两个阶段预算是
    # 1500s + 100s；框架会再加 30s 余量。写小了会在上传素材时被队列硬超时
    # 取消，而那时「提交意图」已经落库 → 被判成「可能已计费」。
    submit_timeout_sec = 1650.0

    def reference_limits_for(self, model_id: str) -> Mapping[str, ReferenceLimit]:
        """按模型返回参考素材分类上限，供受理期与模型目录复用。"""
        profile = resolve_libtv_profile(model_id)
        if profile is None:
            return {}
        return {
            "image": ReferenceLimit(max_count=profile.image_max),
            "video": ReferenceLimit(max_count=profile.video_max),
            "audio": ReferenceLimit(max_count=profile.audio_max),
        }

    async def submit(self, request: GenerationRequest) -> str:
        if request.model == DESUBTITLE_MODEL:
            return await self._submit_desubtitle(request)
        # 归档工作区 id 只能是字符串：模板库那条链把它放在 `archiveRunId`，
        # 通用链没有这个键（None → ""），于是用任务 id 当工作区 id。
        value = str(request.params.get("archiveRunId") or "")
        generic = value != request.request_id
        if generic:
            # 通用生成入口没有模板库写的档案：补出同款工作区，后面的提交、
            # 回执、去重与计费纪律全部照旧。模板库带 archiveRunId 时不进这里。
            await materialize(request)
            value = request.request_id
        path = await asyncio.to_thread(executor.run_path, value)
        account = await asyncio.to_thread(executor.browser_for, path)
        if generic and not await asyncio.to_thread(_request_ready, path):
            # `upload` 只做「传素材 + 估价」，不向上游下单；`submit` 才是花钱
            # 的那一步。缺了它，桥接层会读不到 `inputs/request.json`，报一个
            # 和用户输入完全无关的 ENOENT。
            upload = await executor.execute_stage(value, "upload")
            if upload.code != "OK":
                raise _stage_failure(upload, value, account)
        previous = await asyncio.to_thread(executor.state, path)
        if previous.task_id:
            return f"{value}:{previous.task_id}"
        if previous.status in ("submission_unknown", "submitting"):
            busy = await asyncio.to_thread(process.executor_busy, path)
            if busy:
                raise RateLimited(
                    f"任务 {value} 的上一轮执行器仍在运行，已转入排队，稍后自动重试"
                )
            # 归档记着「回执未知 / 正在提交」：桥接层会拒绝任何重提（可能已扣费）。
            # 若消费明细已能确认命中未认领单就直接接回；未命中则交回人工核对。
            action, task_id = await _resolve_unknown(path, previous, account)
            if action == "adopt":
                # 必须把接回的 task_id 与状态写回 run.json，否则后续 poll 阶段
                # 桥接层读 run.json 看不到 task_id 会直接抛 No task ID。
                await asyncio.to_thread(
                    executor.save_state,
                    path,
                    reconcile_state(previous, adopt_task=task_id, executor_busy=busy),
                )
                return f"{value}:{task_id}"
            raise Conflict(_uncertain_hint(value, account))
        # upload 阶段可能停在「等免费」或「适配器报错」：这时不许再调 submit，
        # 否则桥接层只会抛 "Refusing duplicate or uncertain submission"。
        if generic and previous.status == "waiting_free":
            raise _waiting_free_error(previous)
        if generic and previous.status == "service_error" and previous.last_error:
            raise BadRequest(f"{previous.last_error}；未提交生成")
        if previous.status != "uploaded":
            raise BadRequest("输入尚未完成上传审核，请先继续准备")
        result = await executor.execute_stage(value, "submit")
        latest = await asyncio.to_thread(executor.state, path)
        if latest.task_id:
            return f"{value}:{latest.task_id}"
        if latest.status in ("submitting", "submission_unknown"):
            raise Conflict(_uncertain_hint(value, account))
        if latest.status == "waiting_free":
            raise _waiting_free_error(latest)
        if latest.status == "service_error" and latest.last_error:
            # 适配器给了具体原因（积分队列仍报 0、估价不可用等），原样透出。
            raise BadRequest(f"{latest.last_error}；未提交生成")
        if result.code in ("EXECUTOR_BUSY", "STALE_LOCK"):
            # 两种「没提上去」的原因要分开：账号被别的任务占着是等一等，
            # 遗留锁是要人去核对档案，混成一句话会把人引向错误的动作。
            raise _stage_failure(result, value, account)
        raise BadRequest(f"提交前检查未完成：{result.message or result.code}；未提交生成")

    async def _submit_desubtitle(self, request: GenerationRequest) -> str:
        """去字幕：一条普通生成任务，只是换了模型，**不建运行档案**。

        为什么不走 `upload/submit` 两个阶段：那条路会传素材、编译提示词、写
        `request.json`，而去字幕的输入只有「一个已经存在的 mp4」。没有素材要传，
        再造一套档案只会多一份要对账的状态。

        源视频两种形态：
        - `http(s)` 直链：直接给上游；
        - 对象存储的 key（我们自己的成片）：先取字节落到临时文件，
          由 `desubtitle.mjs` 用 `MediaUploader` 传到上游素材库（含视频审核），
          因为 `create` 只接受上游取得到的 URL。
        """
        source = str(request.params.get("sourceUrl") or "")
        if not source:
            raise BadRequest("去字幕缺少源视频地址（父任务的成片）")

        account = await asyncio.to_thread(executor.select_browser, "free", has_task=False)
        local = source
        temporary: Path | None = None
        try:
            if not source.startswith(("http://", "https://")):
                # 成片在对象存储里是 key（`videos/xxx.mp4`），上游取不到，必须先流式物化到盘，
                # 避免一次性 `get()` 把几百 MB 视频整份读进内存或同步写盘卡住事件循环。
                temporary = Path(tempfile.gettempdir()) / f"desub-{request.request_id}.mp4"
                handle = await asyncio.to_thread(temporary.open, "wb")
                try:
                    async for chunk in get_storage().iter_chunks(source.lstrip("/")):
                        await asyncio.to_thread(handle.write, chunk)
                finally:
                    await asyncio.to_thread(handle.close)
                local = str(temporary)

            result = await executor.submit_desubtitle(
                account=account,
                source=local,
                width=int(request.params.get("width") or 720),
                height=int(request.params.get("height") or 1280),
                duration=float(request.params.get("duration") or 4),
            )
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        task_id = str(result.get("taskId") or "")
        if not task_id:
            raise UpstreamError("去字幕没有返回 task_id")
        logger.info(
            "去字幕已提交 task=%s account=%s power=%s", task_id, account, result.get("power")
        )
        return f"desub|{account}|{task_id}"

    async def _poll_desubtitle(self, external_id: str) -> GenerationProgress:
        _, account, task_id = external_id.split("|", 2)
        items = await libtv_account.progress([task_id], account)
        if not items:
            # 刚提交时上游列表里可能还没有它：按「排队中」处理，下一跳再问。
            return GenerationProgress(
                external_id=external_id, state=GenerationState.RUNNING, queued=True
            )
        item = items[0]
        if item.status == 2:
            return GenerationProgress(
                external_id=external_id,
                state=GenerationState.SUCCEEDED,
                progress=100,
                output_url=item.result_url or None,
                persist_required=True,
            )
        if item.status == 3:
            return GenerationProgress(
                external_id=external_id,
                state=GenerationState.FAILED,
                error=item.failed_reason or "上游去字幕失败",
            )
        return GenerationProgress(
            external_id=external_id,
            state=GenerationState.RUNNING,
            queued=item.queued,
            progress=min(99, item.percent),
        )

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        if external_id.startswith("desub|"):
            return await self._poll_desubtitle(external_id)
        run_id, _, task_id = external_id.partition(":")
        if not task_id:
            raise BadRequest("LibTV 上游任务标识无效")
        path = await asyncio.to_thread(executor.run_path, run_id)
        meta = await asyncio.to_thread(executor.state, path)
        live = None
        if meta.status not in ("success", "failed", "timeout", "generated"):
            account = await asyncio.to_thread(executor.browser_for, path)
            live = await _poll_live(task_id, account)
            if live is not None and live.status in (0, 1):
                return GenerationProgress(
                    external_id=external_id,
                    state=GenerationState.RUNNING,
                    queued=live.queued,
                    progress=min(99, live.percent),
                )
            # 只有终态（成功待下载 / 失败 / 超时）或轻量查询拿不到时才启一次 Node 落盘回执。
            result = await executor.execute_stage(run_id, "poll")
            if result.code != "OK":
                if result.code in EXECUTOR_BLOCKED:
                    return GenerationProgress(
                        external_id=external_id,
                        state=GenerationState.FAILED,
                        error=(
                            f"LibTV 查询中断：{result.message or result.code}；"
                            "上游任务未取消，处理后请点继续查询"
                        ),
                        retryable=False,
                    )
                raise UpstreamError(f"LibTV 查询暂不可用：{result.message or result.code}")
            meta = await asyncio.to_thread(executor.state, path)
        if meta.status == "generated":
            # 下载是独立非计费工作，不占框架每次查询的 60 秒预算。
            #
            # `max_attempts=3`：handler 的注释写着「交给队列重试」，而 1 次就死信
            # 与那句话相反 —— 失败后要等下一跳 poll 重新投递才复活，等于每 5 秒
            # 重试一次。给 3 次（2s/4s 退避）能把浏览器/网络的瞬时抖动挡掉。
            await get_queue().enqueue(
                Job(
                    name="libtv.download",
                    payload={"id": run_id},
                    idempotency_key=f"libtv:download:{run_id}",
                    max_attempts=3,
                    timeout_sec=300,
                )
            )
        status = (
            GenerationState.SUCCEEDED
            if meta.status == "success"
            else GenerationState.FAILED
            if meta.status in ("failed", "timeout")
            else GenerationState.RUNNING
        )
        live_reason = getattr(live, "failed_reason", "") if live is not None else ""
        return GenerationProgress(
            external_id=external_id,
            state=status,
            # 桥接层按 `queueReason` 判定排队：status=0 且没有 reason 说明已经在生成，
            # 与 `QueueItem.queued` 是同一条规则。框架据此顺延墙钟：排队不该算进「生成超时」。
            queued=meta.status == "queued",
            progress=100 if status == GenerationState.SUCCEEDED else min(99, meta.progress_percent),
            output_url=f"/api/v1/templates/replacement-runs/{run_id}/output"
            if status == GenerationState.SUCCEEDED
            else None,
            error=_failed_message(meta, live_reason) if status == GenerationState.FAILED else None,
        )

    async def cancel(self, external_id: str) -> None:
        raise BadRequest("当前适配器不自动取消上游任务，请到 LibTV 核对")


Driver = LibTVProvider
