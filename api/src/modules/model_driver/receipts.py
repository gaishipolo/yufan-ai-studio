"""提交回执的**兼容层**（2026-11-24 起实现已下沉到 `kit/submission_guard.py`）。

台账不是百炼的私有实现，而是所有供应商驱动共用的提交治理件 → 实现搬到
`modules/model_driver/kit/submission_guard.py`（供应商中立层）。本模块保留：

1. **既有 import 路径的转发** —— `from modules.model_driver.receipts import ...`
   继续可用（bailian 驱动、迁移 CLI、既有测试都走这条路径）；
2. **存量文件回执的迁移函数** —— `migrate_legacy_receipts` / `stamp_from_legacy`
   属于「模块层的历史包袱清理」，留在模块层，不进 kit。

状态机的说明（为什么是 DB 唯一约束、`submission_unknown` 为什么不能自动转
submitted 等）见 `kit/submission_guard.py` 的模块 docstring。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_session_factory
from modules.model_driver.kit import submission_guard as _guard
from modules.model_driver.kit.submission_guard import (
    STATUS_REJECTED,
    STATUS_SUBMITTED,
    STATUS_SUBMITTING,
    STATUS_UNKNOWN,
    TERMINAL_STATUSES,
    ReceiptConflict,
    SubmitDecision,
    claim_receipt,
    get_receipt,
    mark_rejected,
    mark_submitted,
    mark_unknown,
    recover_or_reject,
)
from modules.model_driver.models import SubmissionReceipt

__all__ = [
    "STATUS_REJECTED",
    "STATUS_SUBMITTED",
    "STATUS_SUBMITTING",
    "STATUS_UNKNOWN",
    "TERMINAL_STATUSES",
    "ReceiptConflict",
    "SubmitDecision",
    "claim_receipt",
    "decide_before_submit",
    "get_receipt",
    "mark_rejected",
    "mark_submitted",
    "mark_unknown",
    "migrate_legacy_receipts",
    "recover_external_id",
    "recover_or_reject",
    "settle_submission",
    "stamp_from_legacy",
]


async def decide_before_submit(
    *,
    provider: str,
    run_id: str,
    model: str,
    channel_used: str | None = None,
) -> SubmitDecision:
    """转发到 kit 实现，并把**本模块**的 session 工厂传进去。

    为什么要多这一层：既有测试 monkeypatch 的是 `receipts.get_session_factory`
    （本模块的属性）。若直接把 kit 的函数原样转发，函数体在 kit 里取工厂 →
    monkeypatch 失效 → 测试会连上真库而不是内存库。这层转发把「既有 import
    路径」与「既有 monkeypatch 约定」都保持不变，搬迁对调用方透明。
    """
    return await _guard.decide_before_submit(
        provider=provider,
        run_id=run_id,
        model=model,
        channel_used=channel_used,
        factory=get_session_factory(),
    )


async def recover_external_id(*, provider: str, run_id: str, model: str) -> str | None:
    """转发到 kit 实现（见 `decide_before_submit` 的注释：为什么多这一层）。"""
    return await _guard.recover_external_id(
        provider=provider,
        run_id=run_id,
        model=model,
        factory=get_session_factory(),
    )


async def settle_submission(
    *,
    provider: str,
    run_id: str,
    status: str,
    task_id: str | None = None,
    external_id: str | None = None,
    error: str | None = None,
) -> None:
    """转发到 kit 实现（见 `decide_before_submit` 的注释：为什么多这一层）。"""
    await _guard.settle_submission(
        provider=provider,
        run_id=run_id,
        status=status,
        task_id=task_id,
        external_id=external_id,
        error=error,
        factory=get_session_factory(),
    )


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
