"""提交回执的 DB 仓储：抢占、恢复、状态流转。

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

## 冻结窗口

存量文件回执迁移期间由调用方（CLI）保证不再有新的 bailian 提交；本模块不
自己实现 feature flag —— 冻结是**运维动作**，不是运行时开关（见设计文档 §4）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import BadRequest, UpstreamError
from db.base import utcnow
from db.session import get_session_factory
from modules.model_driver.models import SubmissionReceipt

# 与 bailian.py 既有取值逐字一致，迁移时才不会丢语义
STATUS_SUBMITTING = "submitting"
STATUS_SUBMITTED = "submitted"
STATUS_REJECTED = "rejected"
STATUS_UNKNOWN = "submission_unknown"

TERMINAL_STATUSES = frozenset({STATUS_SUBMITTED, STATUS_REJECTED, STATUS_UNKNOWN})


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
) -> SubmissionReceipt:
    """抢占提交权。

    `(provider, run_id)` 唯一约束保证并发下只有一个请求能插进去；
    冲突时**不吞异常、不复用**，由调用方走 `recover_or_reject()` 判定。
    """
    receipt = SubmissionReceipt(
        provider=provider,
        run_id=run_id,
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
    if receipt.status == STATUS_REJECTED:
        return None
    # submitting / submission_unknown / 任何未知状态：一律禁止自动重提
    raise ReceiptConflict(f"{provider} 提交回执状态为 {receipt.status}，禁止自动重提")


async def mark_submitted(
    session: AsyncSession,
    receipt: SubmissionReceipt,
    *,
    task_id: str,
    external_id: str,
) -> None:
    receipt.status = STATUS_SUBMITTED
    receipt.task_id = task_id
    receipt.external_id = external_id
    receipt.submitted_at = utcnow()
    receipt.settled_at = utcnow()
    await session.flush()


async def mark_rejected(session: AsyncSession, receipt: SubmissionReceipt, *, error: str) -> None:
    receipt.status = STATUS_REJECTED
    receipt.error = error[:512]
    receipt.settled_at = utcnow()
    await session.flush()


async def mark_unknown(session: AsyncSession, receipt: SubmissionReceipt, *, error: str) -> None:
    """超时 / 5xx / 响应畸形：可能已受理，禁止自动重提。"""
    receipt.status = STATUS_UNKNOWN
    receipt.error = error[:512]
    receipt.settled_at = utcnow()
    await session.flush()


async def migrate_legacy_receipts(
    session: AsyncSession,
    *,
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    """把存量文件回执迁入 DB。

    `rows` 由 CLI 从工作区读取并校验后传入 —— 本函数只管写库，不读文件
    （工作区可能不在本机，多实例下更不能假设）。

    返回 `{"inserted": n, "rejected": n, "conflicts": n}`：
    - `rejected` 是**输入数据本身非法**（缺 task_id / 状态不认识）
    - `conflicts` 是 DB 里已有同键回执

    ⚠️ 任何 `rejected` / `conflicts` 都必须人工处理完才能解冻提交通道 ——
    跳过一条就等于给二次计费留了个口子。
    """
    stats = {"inserted": 0, "rejected": 0, "conflicts": 0}
    for row in rows:
        status = str(row.get("status") or "")
        if status not in {STATUS_SUBMITTING, STATUS_SUBMITTED, STATUS_REJECTED, STATUS_UNKNOWN}:
            stats["rejected"] += 1
            continue
        if status == STATUS_SUBMITTED and not row.get("task_id"):
            stats["rejected"] += 1
            continue
        provider = str(row.get("provider") or "bailian")
        run_id = str(row.get("run_id") or "")
        if not run_id:
            stats["rejected"] += 1
            continue
        receipt = SubmissionReceipt(
            provider=provider,
            run_id=run_id,
            model=str(row.get("model") or ""),
            status=status,
            task_id=row.get("task_id"),
            external_id=row.get("external_id"),
            channel_used=row.get("channel_used"),
            error=row.get("error"),
            submitted_at=row.get("submitted_at"),
            settled_at=row.get("settled_at"),
        )
        session.add(receipt)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            stats["conflicts"] += 1
            continue
        stats["inserted"] += 1
    return stats


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
) -> SubmitDecision:
    """提交前的幂等门面：**先恢复，抢不到就查明原因**。

    与原来的文件回执语义逐字对齐：
    - 已有 `submitted` → 返回 external_id（驱动直接复用，不调上游）
    - 已有 `rejected`  → 返回 None + claim=None，删掉旧回执后允许重提
    - 已有 `submitting` / `submission_unknown` → 抛 ReceiptConflict 禁止自动重提
    - 无回执 → 抢占并返回 claim

    自己开独立 session：驱动拿不到 runner 的 session，且这个决策必须与
    上游 HTTP 调用**分开事务**（不能让「占了个数据库行」把业务事务拖住）。
    """
    factory = get_session_factory()
    async with factory() as session:
        existing = await recover_or_reject(
            session, provider=provider, run_id=run_id, model=model
        )
        if existing is not None:
            return SubmitDecision(claim=None, external_id=existing)
        # rejected 状态：删掉旧回执，让下面的抢占开始新一轮
        old = await get_receipt(session, provider=provider, run_id=run_id)
        if old is not None and old.status == STATUS_REJECTED:
            await session.delete(old)
            await session.flush()
        claim = await claim_receipt(
            session,
            provider=provider,
            run_id=run_id,
            model=model,
            channel_used=channel_used,
        )
        await session.commit()
        return SubmitDecision(claim=claim, external_id=None)


async def recover_external_id(*, provider: str, run_id: str, model: str) -> str | None:
    """提交前的**只读恢复**：先看有没有已经提交过的任务。

    与 `decide_before_submit` 的区别：这里不抢占，只判断。用于「先做本地校验，
    校验通过再抢占」的场景 —— 校验失败不能留下一个 `submitting` 回执把
    后续合法重试永久锁死。
    """
    factory = get_session_factory()
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
) -> None:
    """提交结束后的状态落库（成功 / 拒绝 / 未知三态）。自己开短事务。"""
    factory = get_session_factory()
    async with factory() as session:
        receipt = await get_receipt(session, provider=provider, run_id=run_id)
        if receipt is None:
            # 没有抢占记录却来结算：只能是迁移期竞态，明确报错而不是静默吞
            raise ReceiptConflict(f"{provider} 结算时找不到回执 run={run_id}")
        if status == STATUS_SUBMITTED:
            if not task_id or not external_id:
                raise ReceiptConflict(f"{provider} 成功态缺少 task_id/external_id")
            await mark_submitted(session, receipt, task_id=task_id, external_id=external_id)
        elif status == STATUS_REJECTED:
            await mark_rejected(session, receipt, error=error or "")
        elif status == STATUS_UNKNOWN:
            await mark_unknown(session, receipt, error=error or "")
        else:
            raise ReceiptConflict(f"{provider} 未知回执状态 {status}")
        await session.commit()


def stamp_from_legacy(raw: dict[str, Any]) -> datetime | None:
    """把存量 JSON 里的时间字符串转成 aware datetime（写库前用）。"""
    value = raw.get("settled_at") or raw.get("submitted_at")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
