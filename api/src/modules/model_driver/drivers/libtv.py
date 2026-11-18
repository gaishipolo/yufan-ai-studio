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
from pathlib import Path

from core.exceptions import BadRequest, Conflict, RateLimited, UpstreamError
from infra import get_queue
from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
    ModelSpec,
)
from infra.queue.base import Job
from modules.model_driver.generic_inputs import materialize
from modules.model_driver.libtv import executor
from modules.model_driver.spec import DriverMeta

META = DriverMeta(
    label="LibTV",
    description="Wan 3.0 视频替换（归档输入 + 免费/积分队列）",
    version="1.0.0",
    author="",
)

MODEL_ID = "libtv-wan3-replacement"

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

_PARAM_SCHEMA = {
    # 键名与 `modules/task/params.py` 对齐：前端按 paramSchema 渲染，不加代码。
    "durationSec": {"type": "int", "min": 2, "max": 30, "step": 1, "default": 5},
    "aspectRatio": {
        "type": "enum",
        "options": ["9:16", "16:9", "1:1"],
        "default": "9:16",
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
    ModelSpec(
        id=MODEL_ID,
        label="LibTV · 720P 视频替换",
        provider="libtv",
        capability=Capability.VIDEO,
        description="720P 视频替换；支持 0 积分畅用队列与积分加速队列",
        param_schema=_PARAM_SCHEMA,
        # 上限按**上游自己的编译器**给，不按记忆里的数字：`runtime/vendor/
        # libtv/prompt-compiler.mjs` 里分类型判 `image ≤ 10 / video ≤ 5 /
        # audio ≤ 5`，总数还卡在 1–15。此前写 5 是把它当成了「图片也最多 5 张」，
        # 于是模板封装的第 6 张参考图在填表页提交时才被受理期挡掉——封装页
        # 明明允许配到 `MAX_SLOT_REFS = 10`，两边口径对不上。取 10 是各类型
        # 里最小那个能共用的值：混合输入也不越界（10 ≤ 15）。
        max_references=10,
        # LibTV 的混合输入通道吃图片、视频与音频：图片是身份 / 商品 / 场景，
        # 视频是原片或动作参考，音频是音轨 / 口型。三类共用同一条 `mixedList`。
        reference_kinds=("image", "video", "audio"),
        # 替换链的模型：有原片 + 素材包的档案工作区，能按模板做替换。
        replacement=True,
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
        f"不想自己核对 → app libtv-reconcile {run_id} --auto（按消费明细自动判断）"
    )


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
    models = MODELS
    # 通用入口要在本跳里先跑 `upload` 再跑 `submit`（模板库那条链把 upload
    # 排成独立的 prepare job，这里没有）。监督进程给的两个阶段预算是
    # 1500s + 100s；框架会再加 30s 余量。写小了会在上传素材时被队列硬超时
    # 取消，而那时「提交意图」已经落库 → 被判成「可能已计费」。
    submit_timeout_sec = 1650.0

    async def submit(self, request: GenerationRequest) -> str:
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
        if previous.status == "submission_unknown":
            # 归档已经记下「回执未知」：桥接层会拒绝任何重提（这是对的，可能已扣费）。
            # 所以这里给的不是「重试」，而是**人工核对后二选一**的解封命令。
            raise Conflict(_uncertain_hint(value, account))
        # upload 阶段可能停在「等免费」或「适配器报错」：这时不许再调 submit，
        # 否则桥接层只会抛 "Refusing duplicate or uncertain submission"。
        if generic and previous.status == "waiting_free":
            # 默认队列就是免费队列，撞上这条的人最多。原文案只说了「报价非零」，
            # 既不说多少钱，也不说还能怎么办 —— 用户唯一能想到的动作是再点一次。
            cost = previous.estimated_credits
            price = f"{cost:g} 积分" if isinstance(cost, (int, float)) else "非零"
            raise BadRequest(
                f"当前没有 0 积分畅用权益，免费队列报价 {price}，未提交；"
                "改用积分队列（billingMode=credits）或等权益恢复后再提交"
            )
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
            cost = latest.estimated_credits
            price = f"{cost:g} 积分" if isinstance(cost, (int, float)) else "非零"
            raise BadRequest(
                f"当前没有 0 积分畅用权益，免费队列报价 {price}，未提交；"
                "改用积分队列（billingMode=credits）或等权益恢复后再提交"
            )
        if latest.status == "service_error" and latest.last_error:
            # 适配器给了具体原因（积分队列仍报 0、估价不可用等），原样透出。
            raise BadRequest(f"{latest.last_error}；未提交生成")
        if result.code in ("EXECUTOR_BUSY", "STALE_LOCK"):
            # 两种「没提上去」的原因要分开：账号被别的任务占着是等一等，
            # 遗留锁是要人去核对档案，混成一句话会把人引向错误的动作。
            raise _stage_failure(result, value, account)
        raise BadRequest(f"提交前检查未完成：{result.message or result.code}；未提交生成")

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        run_id, _, task_id = external_id.partition(":")
        if not task_id:
            raise BadRequest("LibTV 上游任务标识无效")
        path = await asyncio.to_thread(executor.run_path, run_id)
        meta = await asyncio.to_thread(executor.state, path)
        if meta.status != "success":
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
            await get_queue().enqueue(
                Job(
                    name="libtv.download",
                    payload={"id": run_id},
                    idempotency_key=f"libtv:download:{run_id}",
                    max_attempts=1,
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
        return GenerationProgress(
            external_id=external_id,
            state=status,
            # 桥接层把上游 status=0 记成 `queued`（免费队列按名额串行、限流按上游节拍）。
            # 框架据此顺延墙钟：排队不该算进「生成超时」。
            queued=meta.status == "queued",
            progress=100 if status == GenerationState.SUCCEEDED else min(99, meta.progress_percent),
            output_url=f"/api/v1/templates/replacement-runs/{run_id}/output"
            if status == GenerationState.SUCCEEDED
            else None,
            error="LibTV 生成失败，请查看调用档案" if status == GenerationState.FAILED else None,
        )

    async def cancel(self, external_id: str) -> None:
        raise BadRequest("当前适配器不自动取消上游任务，请到 LibTV 核对")


Driver = LibTVProvider
