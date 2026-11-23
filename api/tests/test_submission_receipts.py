"""提交回执 DB 化：守卫「同一 request_id 只提交一次」。

这些测试不是测「表能读写」，而是钉住 2026-11-23 拍板的那条铁律：
**工作区误删后恢复不能再提交一次**（二次计费不可逆）。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import modules.model_driver.receipts as receipts_mod
from core.exceptions import BadRequest
from db.base import Base
from modules.model_driver import models as driver_models  # noqa: F401 — 注册表副作用
from modules.model_driver.receipts import (
    STATUS_REJECTED,
    STATUS_SUBMITTED,
    STATUS_UNKNOWN,
    ReceiptConflict,
    decide_before_submit,
    get_receipt,
    migrate_legacy_receipts,
    settle_submission,
)


def test_receipt_statuses_match_legacy_file_semantics() -> None:
    """状态取值必须与文件回执逐字一致 —— 存量迁移才能不丢语义。"""
    assert STATUS_SUBMITTED == "submitted"
    assert STATUS_REJECTED == "rejected"
    assert STATUS_UNKNOWN == "submission_unknown"




@pytest.fixture
async def session(monkeypatch: pytest.MonkeyPatch) -> AsyncSession:
    """每测试一张干净的 sqlite 内存库。

    `decide_before_submit` / `settle_submission` 自己开 session（驱动拿不到
    runner 的 session），所以这里把模块的 factory 换成指向同一张内存库 ——
    否则测试库与生产库分家，断言全落空。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(receipts_mod, "get_session_factory", lambda: factory)
    async with factory() as db:
        yield db
    await engine.dispose()


async def test_claim_then_settle_reuses_same_external_id(session: AsyncSession) -> None:
    decision = await decide_before_submit(
        provider="bailian", run_id="r" * 32, model="bailian-wan-3.0"
    )
    assert decision.claim is not None
    assert decision.external_id is None

    await settle_submission(
        provider="bailian",
        run_id="r" * 32,
        status=STATUS_SUBMITTED,
        task_id="task-1",
        external_id="r" * 32 + ":task-1",
    )

    again = await decide_before_submit(
        provider="bailian", run_id="r" * 32, model="bailian-wan-3.0"
    )
    # 工作区被删、进程重启、换机器 —— 这些都不影响从 DB 恢复
    assert again.claim is None
    assert again.external_id == "r" * 32 + ":task-1"


async def test_submission_unknown_forbids_auto_retry(session: AsyncSession) -> None:
    """超时 / 5xx：上游可能已受理，绝不能自动重提。"""
    await decide_before_submit(provider="bailian", run_id="u" * 32, model="m")
    await settle_submission(
        provider="bailian", run_id="u" * 32, status=STATUS_UNKNOWN, error="upstream 500"
    )

    with pytest.raises(ReceiptConflict, match="禁止自动重提"):
        await decide_before_submit(provider="bailian", run_id="u" * 32, model="m")


async def test_submitting_also_forbids_auto_retry(session: AsyncSession) -> None:
    """抢占后崩溃在提交中途：同样不能当新任务重提。"""
    await decide_before_submit(provider="bailian", run_id="s" * 32, model="m")

    with pytest.raises(ReceiptConflict, match="禁止自动重提"):
        await decide_before_submit(provider="bailian", run_id="s" * 32, model="m")


async def test_rejected_allows_resubmit_and_clears_old_error(session: AsyncSession) -> None:
    """明确拒绝（4xx）= 没受理，允许人工重提，且旧错误要清掉。"""
    await decide_before_submit(provider="bailian", run_id="j" * 32, model="m")
    await settle_submission(
        provider="bailian", run_id="j" * 32, status=STATUS_REJECTED, error="bad params"
    )

    decision = await decide_before_submit(provider="bailian", run_id="j" * 32, model="m")
    assert decision.claim is not None  # 重新拿到提交权
    receipt = await get_receipt(session, provider="bailian", run_id="j" * 32)
    assert receipt is not None
    assert receipt.error is None


async def test_model_change_on_existing_task_is_rejected(session: AsyncSession) -> None:
    """同一 run 换了模型重提 = 拿旧任务号查新模型，必须拒绝。"""
    await decide_before_submit(provider="bailian", run_id="c" * 32, model="model-a")
    await settle_submission(
        provider="bailian",
        run_id="c" * 32,
        status=STATUS_SUBMITTED,
        task_id="t",
        external_id="ext",
    )

    with pytest.raises(BadRequest, match="模型不能变更"):
        await decide_before_submit(provider="bailian", run_id="c" * 32, model="model-b")


async def test_migration_rejects_invalid_rows_without_skipping(session: AsyncSession) -> None:
    """存量回执不许跳过坏行 —— 跳过一条就是给二次计费留口子。"""
    stats = await migrate_legacy_receipts(
        session,
        rows=[
            {"run_id": "a" * 32, "status": "submitted", "task_id": "t"},  # ok
            {"run_id": "b" * 32, "status": "submitted", "task_id": ""},  # 损坏
            {"run_id": "", "status": "submitted", "task_id": "t"},  # 缺 run_id
        ],
    )
    assert stats == {"inserted": 1, "rejected": 2, "conflicts": 0}
    assert await get_receipt(session, provider="bailian", run_id="b" * 32) is None


async def test_channel_used_survives_recovery(session: AsyncSession) -> None:
    """通道选择必须随回执落库 —— 重试不能换成另一条通道。"""
    await decide_before_submit(
        provider="bailian", run_id="h" * 32, model="m", channel_used="oss"
    )
    receipt = await get_receipt(session, provider="bailian", run_id="h" * 32)
    assert receipt is not None
    assert receipt.channel_used == "oss"
