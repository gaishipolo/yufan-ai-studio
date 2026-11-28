"""提交台账：抢占、恢复、状态流转（所有供应商驱动共用）。

> 2026-11-24（D5）本模块从 `modules/model_driver/receipts.py` 下沉到 `kit/`：
> 台账不是百炼的私有实现，而是**所有供应商驱动共用的提交治理件** —— 谁提交、
> 提交到哪、能不能重提，都由这一套状态机说了算。

## 为什么是 DB 唯一约束，不是文件 `O_EXCL`

文件回执和运行工作区同生共死，工作区误删后恢复路径读不到回执，会把同一个
`request_id` 重新提交给上游 —— 二次计费。DB 唯一约束有事务、有备份、多实例一致。

## 状态机（沿用 bailian 既有语义）

```
(无) ──claim──> submitting ──成功──> submitted
                   │                    │
            上游 4xx │             恢复时复用
                   v
                rejected  ──人工重提──> (无)

            超时/5xx/畸形 ──> submission_unknown ──> 禁止自动重提
```

`submission_unknown` 永远不能自动转 submitted —— 请求可能已被受理，自动重试
就是二次计费。只能人工核对上游后处置。

## 与框架层 `submit_attempts` 的关系（别当成两套重复机制）

框架层已经在记录侧挡了同一件事：`submit_attempts > 0` 且无 `external_id` →
「提交结果未知」，**禁止自动重提**（见 `generation/spec.py::submit_unknown`）。
两者维度不同：

| | 键 | 记什么 |
| --- | --- | --- |
| 框架 `submit_attempts` | 业务记录 id（`record.id`） | 「这条记录提交过没有」 |
| 本台账 | `(provider, run_id)` | 「这个供应商的这个任务提交到哪了」+ 三态细节 |

本台账多出来的东西是**状态细节**（`submitting` / `rejected` / `submission_unknown`
与 `error` 原文），以及**在驱动拿到 task_id 的瞬间就落库**（而不是等框架收到
submit 返回值再落）—— 后者把「上游已受理、进程在写库前崩了」的窗口消掉了。

## 两种结算形态（同步 / 异步）

台账不只服务异步提交：

| 形态 | 驱动返回 | `task_id` | `external_id` |
| --- | --- | --- | --- |
| 异步 | 上游任务号（字符串） | 任务号 | 同一个值 |
| 同步 | 终态快照（`GenerationProgress`） | 空 | 快照自带的标识 |

两种形态都**必须有 `external_id`** —— 它是「恢复时能续接」的凭证，缺了这行台账
就等于没提交过。

## 轮次键：为什么不是 `record_id` 本身

台账唯一键是 `(provider, run_id)`。百炼的 `run_id` 是**归档 run id = 幂等键**；
其余驱动没有归档 run，能用的只有业务记录 id，而**同一份记录会被提交多次**：

- 4xx 之后框架自动重试 3 次；
- 人工重提（`modules/image/service.py::retry`）**复用同一 `job_id`**，清掉
  `external_id` 后重新提交。

所以这些驱动的键是 `{record_id}#{轮次}`：**每一轮一行**，上一轮的历史留着可查。
若直接拿 `record_id` 当键，人工重提会被上一轮的台账误挡（返回旧 external_id
→ 用户拿回上一轮的图）。

## 「这一轮有没有可能被受理」用哪份判定

用 `infra/delivery.py::possibly_delivered` —— 与框架
（`generation/runner.py::_possibly_delivered`）**同一份实现**。两边判定必须一致：
框架判「可安全重试」而台账判「可能已受理」时，人会同时看到「任务失败」与
「可能已计费」两句矛盾的话。

## 冻结窗口

存量文件回执迁移期间由调用方（CLI）保证不再有新的 bailian 提交；本模块不
自己实现 feature flag —— 冻结是**运维动作**，不是运行时开关（见设计文档 §4）。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.exceptions import BadRequest, UpstreamError
from core.logging import get_logger
from db.base import utcnow
from db.session import get_session_factory
from infra.ai.base import GenerationProgress
from infra.delivery import possibly_delivered
from modules.model_driver.models import SubmissionReceipt

logger = get_logger(__name__)

# 与 bailian.py 既有取值逐字一致，迁移时才不会丢语义
STATUS_SUBMITTING = "submitting"
STATUS_SUBMITTED = "submitted"
STATUS_REJECTED = "rejected"
STATUS_UNKNOWN = "submission_unknown"
#: 人工重提时上一轮的凭证被显式作废。**不是失败**，是「这一轮不算数了」：
#: 恢复器不能拿它回填，抢占时也不能把它当成「已提交」复用。旧行列保留可查。
STATUS_SUPERSEDED = "superseded"

TERMINAL_STATUSES = frozenset(
    {STATUS_SUBMITTED, STATUS_REJECTED, STATUS_UNKNOWN, STATUS_SUPERSEDED}
)


class ReceiptConflict(UpstreamError):
    """已有提交记录但不是可复用状态 —— 禁止自动重提。"""


async def get_receipt(
    session: AsyncSession, *, provider: str, run_id: str
) -> SubmissionReceipt | None:
    result = await session.execute(
        select(SubmissionReceipt).where(
            SubmissionReceipt.provider == provider,
            SubmissionReceipt.run_id == run_id,
        )
    )
    return result.scalar_one_or_none()


async def claim_receipt(
    session: AsyncSession,
    *,
    provider: str,
    run_id: str,
    model: str,
    channel_used: str | None = None,
    record_id: str | None = None,
) -> SubmissionReceipt:
    """抢占提交权。

    `(provider, run_id)` 唯一约束保证并发下只有一个请求能插进去；
    冲突时**不吞异常、不复用**，由调用方走 `recover_or_reject()` 判定。

    `record_id` = **业务记录 id**（台账里反查业务表的那一列）：恢复器靠它把
    「已提交但业务表没记上」的凭证找回来。键（`run_id`）与业务记录 id 是两回事
    —— 百炼的键是幂等键（= `request.request_id`），轮次键的驱动用
    `{record_id}#{轮次}`。
    """
    receipt = SubmissionReceipt(
        provider=provider,
        run_id=run_id,
        record_id=record_id,
        model=model,
        status=STATUS_SUBMITTING,
        channel_used=channel_used,
    )
    session.add(receipt)
    try:
        await session.flush()
    except IntegrityError as exc:
        # 唯一约束冲突：回执已存在。**必须 rollback 这个失败的事务**，否则
        # session 进入 failed 状态，后续任何查询都抛 PendingRollbackError。
        # 这里的 session 是本模块自己开的（见 claim_for_submit），不是 runner
        # 持有的那个 —— 回滚它不会动业务记录。
        await session.rollback()
        raise ReceiptConflict(f"{provider} 已有提交记录；禁止自动重提") from exc
    return receipt


async def recover_or_reject(
    session: AsyncSession,
    *,
    provider: str,
    run_id: str,
    model: str,
) -> str | None:
    """从回执恢复 external_id；不可恢复时抛异常。

    - `submitted`    → 返回既有 external_id（模型变更要拒绝，防止同一 run 换模型重提）
    - `rejected`     → 返回 None，允许调用方重新走提交流程
    - `submitting` / `submission_unknown` → 抛 ReceiptConflict，**禁止自动重提**
    """
    receipt = await get_receipt(session, provider=provider, run_id=run_id)
    if receipt is None:
        return None
    if receipt.status == STATUS_SUBMITTED:
        if receipt.model != model:
            raise BadRequest(f"{provider} 已提交任务的模型不能变更")
        if not receipt.external_id:
            # 状态说 submitted 却没有 id —— 数据损坏，不能当新任务提交
            raise ReceiptConflict(f"{provider} 回执损坏：已提交但没有任务号")
        return receipt.external_id
    if receipt.status in (STATUS_REJECTED, STATUS_SUPERSEDED):
        # superseded = 人工明确作废了上一轮。此时**必须**返回 None 让新的一轮
        # 真的发出 POST —— 返回旧任务号正是「点了重提却什么也没发生」的根因。
        return None
    # submitting / submission_unknown / 任何未知状态：一律禁止自动重提
    raise ReceiptConflict(f"{provider} 提交回执状态为 {receipt.status}，禁止自动重提")


async def mark_submitted(
    session: AsyncSession,
    receipt: SubmissionReceipt,
    *,
    external_id: str,
    task_id: str | None = None,
) -> None:
    """结算为「已提交」。

    **同步形态**（`task_id` 为空）：同步生图供应商在 `submit` 里就等到了结果，
    没有独立任务号 —— 此时 `external_id` 记的是供应商给的任务号/请求标识，
    `task_id` 留空。**异步形态**：两者都有。

    `external_id` 两种形态都必须有 —— 它是「恢复时能续接」的凭证，缺了这条
    台账行就等于没提交过。
    """
    receipt.status = STATUS_SUBMITTED
    receipt.task_id = task_id
    receipt.external_id = external_id
    receipt.submitted_at = utcnow()
    receipt.settled_at = utcnow()
    await session.flush()


#: 台账 `error` 的统一口径：`{类型}: {消息}`。
#:
#: 只有这两个 mark 函数写这一列（全仓搜 `receipt.error =` 可核），所以口径收敛在
#: 一处。两类来源：
#: - 异常路径（ `_settle_failure` ）传 `TimeoutError: 连接超时` 这类 `类型: 消息`，
#:   原样保留 —— 类型就是真实异常类名；
#: - 业务判定（「提交返回成功，但没有可续接的标识」等）没有异常对象，补
#:   `业务判定:` 前缀。**不冒充异常类名**：运维看到 `BadRequest:` 会去找抛错代码，
#:   而那其实是框架对结果的判断，没有异常。
#:
#: 这列只供人读、不参与任何判定（`STATUS_*` 才是状态机），所以统一的是可读性，
#: 不改变对外行为，也不用动契约。
_ERROR_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*: |^业务判定: ")


def _normalized_error(error: str) -> str:
    text = error.strip()
    if not text:
        return ""
    if not _ERROR_PREFIX.match(text):
        text = f"业务判定: {text}"
    return text[:512]


async def mark_rejected(session: AsyncSession, receipt: SubmissionReceipt, *, error: str) -> None:
    receipt.status = STATUS_REJECTED
    receipt.error = _normalized_error(error)
    receipt.settled_at = utcnow()
    await session.flush()


async def mark_unknown(session: AsyncSession, receipt: SubmissionReceipt, *, error: str) -> None:
    """超时 / 5xx / 响应畸形：可能已受理，禁止自动重提。"""
    receipt.status = STATUS_UNKNOWN
    receipt.error = _normalized_error(error)
    receipt.settled_at = utcnow()
    await session.flush()


class SubmitDecision:
    """提交前的裁决结果。驱动只看这一个值，不碰 session。"""

    def __init__(self, *, claim: SubmissionReceipt | None, external_id: str | None) -> None:
        self.claim = claim  # 非 None = 本次调用赢得了提交权
        self.external_id = external_id  # 非 None = 已有任务，直接恢复轮询


async def decide_before_submit(
    *,
    provider: str,
    run_id: str,
    model: str,
    channel_used: str | None = None,
    record_id: str | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> SubmitDecision:
    """提交前的幂等门面：**先恢复，抢不到就查明原因**。

    与原来的文件回执语义逐字对齐：
    - 已有 `submitted` → 返回 external_id（驱动直接复用，不调上游）
    - 已有 `rejected` / `superseded` → 删掉旧回执后抢占新一轮
    - 已有 `submitting` / `submission_unknown` → 抛 ReceiptConflict 禁止自动重提

    `superseded` 是人工重提时显式作废的上一轮（`abandon_receipt`）。正常路径下
    它的键已经被挪走，这里不会再撞见；仍保留这一分支是为了兜住「作废与抢占
    竞态」—— 不能因为一行历史记录把用户刚确认的重提卡死。
    - 无回执 → 抢占并返回 claim

    自己开独立 session：驱动拿不到 runner 的 session，且这个决策必须与
    上游 HTTP 调用**分开事务**（不能让「占了个数据库行」把业务事务拖住）。

    `factory` 只为测试注入（兼容层用它在转发时把调用方的工厂传进来）——
    生产路径一律用默认的全局工厂。

    `record_id` 落进新抢占的那一行（业务记录 id）→ 崩溃恢复器能按它找回凭证。
    """
    factory = factory or get_session_factory()
    async with factory() as session:
        existing = await recover_or_reject(
            session, provider=provider, run_id=run_id, model=model
        )
        if existing is not None:
            return SubmitDecision(claim=None, external_id=existing)
        # rejected / superseded：删掉旧回执，让下面的抢占开始新一轮
        old = await get_receipt(session, provider=provider, run_id=run_id)
        if old is not None and old.status in (STATUS_REJECTED, STATUS_SUPERSEDED):
            await session.delete(old)
            await session.flush()
        claim = await claim_receipt(
            session,
            provider=provider,
            run_id=run_id,
            model=model,
            channel_used=channel_used,
            record_id=record_id,
        )
        await session.commit()
        return SubmitDecision(claim=claim, external_id=None)


async def recover_external_id(
    *,
    provider: str,
    run_id: str,
    model: str,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> str | None:
    """提交前的**只读恢复**：先看有没有已经提交过的任务。

    与 `decide_before_submit` 的区别：这里不抢占，只判断。用于「先做本地校验，
    校验通过再抢占」的场景 —— 校验失败不能留下一个 `submitting` 回执把
    后续合法重试永久锁死。
    """
    factory = factory or get_session_factory()
    async with factory() as session:
        return await recover_or_reject(session, provider=provider, run_id=run_id, model=model)


async def settle_submission(
    *,
    provider: str,
    run_id: str,
    status: str,
    task_id: str | None = None,
    external_id: str | None = None,
    error: str | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """提交结束后的状态落库（成功 / 拒绝 / 未知三态）。自己开短事务。"""
    factory = factory or get_session_factory()
    async with factory() as session:
        receipt = await get_receipt(session, provider=provider, run_id=run_id)
        if receipt is None:
            # 没有抢占记录却来结算：只能是迁移期竞态，明确报错而不是静默吞
            raise ReceiptConflict(f"{provider} 结算时找不到回执 run={run_id}")
        if status == STATUS_SUBMITTED:
            # `task_id` 可空（同步形态：submit 里就等到了结果，没有独立任务号）；
            # `external_id` 两种形态都必须有 —— 它是恢复时能续接的凭证。
            if not external_id:
                raise ReceiptConflict(f"{provider} 成功态缺少 external_id")
            await mark_submitted(session, receipt, task_id=task_id, external_id=external_id)
        elif status == STATUS_REJECTED:
            await mark_rejected(session, receipt, error=error or "")
        elif status == STATUS_UNKNOWN:
            await mark_unknown(session, receipt, error=error or "")
        else:
            raise ReceiptConflict(f"{provider} 未知回执状态 {status}")
        await session.commit()


#: 轮次键的分隔符：`{record_id}#{轮次}`
_ATTEMPT_SEPARATOR = "#"

R = TypeVar("R")


async def _next_attempt(
    factory: async_sessionmaker[AsyncSession], *, provider: str, record_id: str
) -> int:
    """这个业务记录已经提交过几轮 → 下一轮的轮次号。

    按**已有台账行数**算。台账是**追加式**的历史 —— 每一轮都留一行（包括被
    明确拒绝的那几轮），所以行数就是「已经发生过几轮」。轮次号只保证同一记录
    的行不撞键，不承担幂等职责（幂等由框架层的 `submit_attempts` + 本台账的
    唯一约束共同承担）。
    """
    async with factory() as session:
        result = await session.execute(
            select(func.count())
            .select_from(SubmissionReceipt)
            .where(
                SubmissionReceipt.provider == provider,
                SubmissionReceipt.record_id == record_id,
            )
        )
        return int(result.scalar_one()) + 1


def _identifiers(result: object) -> tuple[str | None, str | None]:
    """从驱动的返回值里取出 `(task_id, external_id)`。

    - **字符串**：驱动返回的是上游任务号（异步形态）→ 它既是 `task_id` 也是
      续接凭证，所以两者是同一个值。
    - **`GenerationProgress`**：终态快照（同步形态）→ 没有独立任务号，
      `task_id` 留空，`external_id` 取快照自带的标识。
    """
    if isinstance(result, str):
        return result, result
    if isinstance(result, GenerationProgress):
        return None, result.external_id
    return None, None


async def guarded_submit(
    *,
    provider: str,
    record_id: str,
    model: str,
    channel_used: str | None = None,
    call: Callable[[], Awaitable[R]],
    precheck: Callable[[], Awaitable[None]] | None = None,
    run_id: str | None = None,
    identify: Callable[[R], tuple[str | None, str | None]] | None = None,
    existing_result: Callable[[str], R] | None = None,
    unknown_error: Callable[[Exception], Exception] | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> R:
    """把一次提交尝试纳入台账，并在拿到上游标识的**第一时间**落库。

    ## 顺序（所有驱动一致）

    `恢复/轮次键 → precheck（本地校验）→ 抢占 → 付费 POST → 结算`

    百炼的本地校验/素材准备必须发生在抢占之前，否则校验失败会留下
    `submitting` 回执，把后续合法重试永久锁死。`precheck` 就是给这一步的缝：
    它抛异常时不写任何台账；通过后 guard 才抢占。

    ## 三态怎么判

    失败时用 `infra/delivery.py::possibly_delivered`（与框架同一份实现）：

    - 「确定没送达」（4xx / 连接都没建起来）→ 记 `rejected`：**可以安全重试**。
    - 「可能已受理」（5xx / 超时 / 读响应失败）→ 记 `submission_unknown`：
      **禁止自动重提**，只能人工核对。

    ## 轮次

    默认键是 `{record_id}#{轮次}`，轮次 = 该记录已有的台账行数 + 1。每轮一行，
    上一轮的历史留着可查；人工重提会开新的一轮，不会被旧行挡住。

    `run_id` 给「台账键就是归档 run id」的驱动（百炼）覆盖；它们自己负责
    只读恢复与本地校验，base guard 仍负责抢占、调用、结算与三态。

    ## 已有提交

    `decide_before_submit` 发现同一键已 `submitted` → 默认抛 `ReceiptConflict`。
    调用方若能把 external_id 还原成自己的返回类型（百炼返回字符串），传
    `existing_result` 复用；正常路径下仍应先做只读恢复（百炼在 guard 外做）。

    ## 调用失败

    `unknown_error` 只在台账判为 `submission_unknown` 时用来替换面向用户的异常；
    其余异常保持原样。落账失败不掩盖原始异常。
    """
    factory = factory or get_session_factory()
    if not record_id.strip():
        # 没有业务记录 id → 入不了账（台账的键就是它）。这时**不做台账**、
        # 直接提交：台账是账本，不该拦停正常业务。
        if precheck is not None:
            await precheck()
        logger.warning(f"{provider} 提交缺少 record_id，本次提交不记入台账")
        return await call()

    if run_id is None:
        attempt = await _next_attempt(factory, provider=provider, record_id=record_id)
        run_id = f"{record_id}{_ATTEMPT_SEPARATOR}{attempt}"

    if precheck is not None:
        await precheck()

    decision = await decide_before_submit(
        provider=provider,
        run_id=run_id,
        model=model,
        channel_used=channel_used,
        record_id=record_id,
        factory=factory,
    )
    if decision.external_id is not None:
        if existing_result is not None:
            return existing_result(decision.external_id)
        raise ReceiptConflict(f"{provider} 已有提交记录；禁止自动重提")

    try:
        result = await call()
    except Exception as exc:
        status = await settle_failure(
            provider=provider, run_id=run_id, exc=exc, factory=factory
        )
        if status == STATUS_UNKNOWN and unknown_error is not None:
            raise unknown_error(exc) from exc
        raise

    if identify is not None:
        task_id, external_id = identify(result)
    else:
        task_id, external_id = _identifiers(result)
    if not external_id:
        # 提交成功却没拿到可续接的标识：不能记「已提交」（缺凭证的行等于没提交过），
        # 也不能记「已拒绝」（上游确实受理了）→ 记「可能已受理」，让人来核对。
        await settle_submission(
            provider=provider,
            run_id=run_id,
            status=STATUS_UNKNOWN,
            error="提交返回成功，但没有可续接的标识",
            factory=factory,
        )
        return result

    await settle_submission(
        provider=provider,
        run_id=run_id,
        status=STATUS_SUBMITTED,
        task_id=task_id,
        external_id=external_id,
        factory=factory,
    )
    return result


async def settle_failure(
    *,
    provider: str,
    run_id: str,
    exc: Exception,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> str:
    """把失败那一轮记成 `rejected` / `submission_unknown`，返回落下的状态。

    ## 与 `guarded_submit` 用的是**同一份**判定

    `infra/delivery.py::possibly_delivered`：

    - 「确定没送达」（4xx / 连接都没建起来）→ `rejected`：**可以安全重试**；
    - 「可能已受理」（5xx / 超时 / 读响应失败 / 响应畸形）→ `submission_unknown`：
      **禁止自动重提**，只能人工核对。

    ## 给谁用

    `guarded_submit` 内部调用它；需要自己处理「可能已受理」文案的驱动
    （百炼）通过 `unknown_error` 回调把异常换成面向用户的说明。

    返回值让调用方据此组织措辞。落库失败**不掩盖原始异常** —— 上游的错误
    信息比「台账写不进去」重要得多。
    """
    status = STATUS_UNKNOWN if possibly_delivered(exc) else STATUS_REJECTED
    error = f"{type(exc).__name__}: {exc}"
    try:
        await settle_submission(
            provider=provider,
            run_id=run_id,
            status=status,
            error=error,
            factory=factory,
        )
    except Exception:
        logger.exception(f"{provider} 提交失败后写入台账失败 run={run_id}")
    return status


async def find_recoverable_external_id(
    record_id: str,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> str | None:
    """按业务记录 id 找**最后一次提交尝试**留下的可续接凭证。

    ## 用途：崩溃恢复

    驱动的 `submit` 拿到上游标识的**同一个瞬间**就落台账（D5 第二步），而业务
    表的 `record.external_id` 是 submit 返回**之后**才写 —— 中间崩了，业务表就
    说「没提交过」，台账却记着「提交过、凭证在这儿」。恢复器用这个函数把凭证
    取回来回填业务表，然后照常续接轮询。

    ## 只认最后一次尝试

    台账是**追加式**历史：同一记录可能有多轮（4xx 后框架自动重试 3 次、
    人工重提复用同一个 `record_id`）。所以只看**最后一轮**：

    - 最后一轮 `submitted` → 这一轮确实提交成功了 → 返回它的凭证；
    - 最后一轮 `rejected` / `submission_unknown` / `submitting` → 最后一轮没
      拿到凭证 → 返回 `None`。**不回退到更早的成功轮次** —— 那是上一轮的
      结果，而人工重提的语义正是「不要上一轮，要新的」。

    ## 只认「异步形态」的行

    台账里 `task_id` 为空 = 这次提交没有独立任务号（**同步形态**：submit 里就等到
    了结果）。这种行**不回填**：

    - 同步形态的凭证撑不起「续接轮询」—— 回填了也只是把「提交结果未知，等人核对」
      变成「同步供应商孤儿，判失败」，语义退化；
    - 不回填时框架会禁止自动重提（避免二次计费），把人交给核对流程 —— 这才是
      同步形态崩溃后该走的路。

    ## 找不到就是没有

    返回 `None` 时调用方应保持现状（判「提交结果未知」，等人工核对），
    **不要编一个凭证**。
    """
    if not record_id.strip():
        return None
    factory = factory or get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(SubmissionReceipt)
            .where(SubmissionReceipt.record_id == record_id)
            .order_by(SubmissionReceipt.created_at.desc())
            .limit(1)
        )
        receipt = result.scalar_one_or_none()
    if receipt is None or receipt.status != STATUS_SUBMITTED:
        return None
    if not receipt.task_id or not receipt.external_id:
        return None
    return receipt.external_id


async def abandon_receipt(
    record_id: str,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """人工重提：把这条业务记录的既有凭证整批作废，返回作废行数。

    ## 为什么必须显式作废

    百炼的台账键就是业务记录 id（`run_id = request.request_id`）。人工重提清掉
    业务表的 `external_id`，但台账里上一轮的 `submitted` 行原封不动 —— 下一次
    `submit` 的只读恢复（`recover_external_id`）就会把**上一轮的任务号**捡回来，
    一次新的付费 POST 都不发。用户看到的现象是「点了重新生成，返回的还是上一条
    失败任务」。其余驱动用 `{record_id}#{轮次}` 键天然不受影响，只有百炼撞这个坑。

    ## 为什么状态改成 `superseded` 而不是删除

    账单对不上时要能查到上一轮的任务号。旧行留着，但：

    - `recover_or_reject` 对它返回 `None`（可开新一轮，不是可复用的凭证）；
    - `find_recoverable_external_id` 只认最后一轮且必须 `submitted`，它已不是；
    - `run_id` 换成带短随机后缀的历史键，让原键能被新一轮 `claim_receipt` 抢占
      （唯一约束在 `(provider, run_id)` 上）。

    `run_id` 列是 `String(64)`，截断到 48 字再补后缀，避免长 id 被数据库截断。
    """
    if not record_id.strip():
        return 0
    factory = factory or get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(SubmissionReceipt).where(
                SubmissionReceipt.record_id == record_id,
                SubmissionReceipt.status != STATUS_SUPERSEDED,
            )
        )
        rows = result.scalars().all()
        for receipt in rows:
            receipt.run_id = f"{receipt.run_id[:48]}#x{uuid.uuid4().hex[:8]}"
            receipt.status = STATUS_SUPERSEDED
            receipt.settled_at = utcnow()
        if rows:
            await session.commit()
        return len(rows)
