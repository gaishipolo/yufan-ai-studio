"""提交台账（kit/submission_guard）：状态机语义 + 同步/异步两种结算形态。

台账不是百炼的私有实现，而是所有供应商驱动共用的提交治理件 —— 所以这里测的
是**语义**（幂等、禁止自动重提、两种结算形态），不是「某张表能读写」。

对照：`tests/test_submission_receipts.py` 走的是**兼容层**入口（`receipts.py`），
本文件走 **kit 直连**入口。两边指向同一份实现 → 最后一条测试专门钉住
「两条入口行为一致」，防止有人只改一边。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import modules.model_driver.kit.submission_guard as guard_mod
import modules.model_driver.receipts as receipts_mod
from core.exceptions import BadRequest
from db.base import Base
from modules.model_driver import models as driver_models  # noqa: F401 — 注册表副作用
from modules.model_driver.kit.submission_guard import (
    STATUS_REJECTED,
    STATUS_SUBMITTED,
    STATUS_UNKNOWN,
    ReceiptConflict,
    decide_before_submit,
    get_receipt,
    recover_external_id,
    settle_submission,
)

RUN_ID = "r" * 32
MODEL = "bailian-wan-3.0"


@pytest.fixture
async def session(monkeypatch: pytest.MonkeyPatch) -> AsyncSession:
    """每测试一张干净的 sqlite 内存库（kit 入口）。

    kit 的三个门面自己开 session（驱动拿不到 runner 的 session），所以把
    **kit 模块**的 factory 换成指向同一张内存库 —— 否则断言全落空。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(guard_mod, "get_session_factory", lambda: factory)
    async with factory() as db:
        yield db
    await engine.dispose()


def test_status_values_are_stable() -> None:
    """状态取值是**跨模块契约**（存量迁移、审计查询都按字面值认）。

    改这里的字面值 = 让存量回执无法识别 → 必须当成破坏性变更。
    """
    assert STATUS_SUBMITTED == "submitted"
    assert STATUS_REJECTED == "rejected"
    assert STATUS_UNKNOWN == "submission_unknown"


async def test_claim_then_settle_is_idempotent(session: AsyncSession) -> None:
    """抢占 → 结算 → 再提交：拿到既有 external_id，**不再抢占**。

    这是「工作区被删、进程重启、换机器也不重复提交」的守卫。
    """
    decision = await decide_before_submit(provider="bailian", run_id=RUN_ID, model=MODEL)
    assert decision.claim is not None
    assert decision.external_id is None

    await settle_submission(
        provider="bailian",
        run_id=RUN_ID,
        status=STATUS_SUBMITTED,
        task_id="task-1",
        external_id=f"{RUN_ID}:task-1",
    )

    again = await decide_before_submit(provider="bailian", run_id=RUN_ID, model=MODEL)
    assert again.claim is None
    assert again.external_id == f"{RUN_ID}:task-1"


async def test_unknown_state_forbids_auto_retry(session: AsyncSession) -> None:
    """超时 / 5xx：上游可能已受理 → 禁止自动重提（重提就是第二份账单）。"""
    await decide_before_submit(provider="bailian", run_id="u" * 32, model=MODEL)
    await settle_submission(
        provider="bailian", run_id="u" * 32, status=STATUS_UNKNOWN, error="timeout"
    )

    with pytest.raises(ReceiptConflict):
        await decide_before_submit(provider="bailian", run_id="u" * 32, model=MODEL)


async def test_rejected_state_allows_a_new_attempt(session: AsyncSession) -> None:
    """明确被拒（4xx）：上游没受理 → 允许重新提交一次。"""
    await decide_before_submit(provider="bailian", run_id="j" * 32, model=MODEL)
    await settle_submission(
        provider="bailian", run_id="j" * 32, status=STATUS_REJECTED, error="参数错"
    )

    decision = await decide_before_submit(provider="bailian", run_id="j" * 32, model=MODEL)
    assert decision.claim is not None
    assert decision.external_id is None


async def test_sync_shape_settles_without_task_id(session: AsyncSession) -> None:
    """**同步形态**：提交即出图，没有独立任务号 → `task_id` 留空也算成功。

    同步生图供应商在 `submit` 里就等到了结果，所以拿不到「异步任务号」。
    台账要能表达这一形态，否则同步驱动接不进台账。
    """
    await decide_before_submit(provider="agnes", run_id="s" * 32, model="agnes-image")
    await settle_submission(
        provider="agnes",
        run_id="s" * 32,
        status=STATUS_SUBMITTED,
        task_id=None,
        external_id="agnes-task-9",
    )

    receipt = await get_receipt(session, provider="agnes", run_id="s" * 32)
    assert receipt is not None
    assert receipt.status == STATUS_SUBMITTED
    assert receipt.task_id is None
    assert receipt.external_id == "agnes-task-9"

    # 同步形态同样能被恢复（靠 external_id 续接）
    decision = await decide_before_submit(provider="agnes", run_id="s" * 32, model="agnes-image")
    assert decision.external_id == "agnes-task-9"


async def test_successful_settlement_requires_external_id(session: AsyncSession) -> None:
    """`external_id` 两种形态都必须有 —— 它是恢复时能续接的凭证。

    缺了就等于「提交过但接不回来」，台账行是废的 → 明确报错，不静默吞。
    """
    await decide_before_submit(provider="agnes", run_id="e" * 32, model="agnes-image")

    with pytest.raises(ReceiptConflict):
        await settle_submission(
            provider="agnes",
            run_id="e" * 32,
            status=STATUS_SUBMITTED,
            task_id="task-1",
            external_id="",
        )


async def test_recover_external_id_is_read_only(session: AsyncSession) -> None:
    """只读恢复：**不抢占**。没有回执就返回 None，不留下 `submitting` 残骸。

    场景：驱动要先做本地校验（校验失败不该把后续合法重试永久锁死）。
    """
    assert await recover_external_id(provider="bailian", run_id=RUN_ID, model=MODEL) is None

    receipt = await get_receipt(session, provider="bailian", run_id=RUN_ID)
    assert receipt is None, "只读恢复不该创建回执行"


async def test_model_change_on_a_submitted_run_is_rejected(session: AsyncSession) -> None:
    """已提交的任务不允许换模型重提（同一 run 换模型 = 另一件事）。"""
    await decide_before_submit(provider="bailian", run_id=RUN_ID, model=MODEL)
    await settle_submission(
        provider="bailian",
        run_id=RUN_ID,
        status=STATUS_SUBMITTED,
        task_id="task-1",
        external_id="ext-1",
    )

    with pytest.raises(BadRequest):
        await decide_before_submit(provider="bailian", run_id=RUN_ID, model="other-model")


async def test_compat_layer_and_kit_behave_identically(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """兼容层（`receipts.py`）与 kit 直连**行为一致**。

    两条入口指向同一份实现，但走的是不同的工厂注入路径（兼容层把调用方的
    factory 转发进 kit）→ 必须钉住，防止有人只改一边。
    """
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    monkeypatch.setattr(receipts_mod, "get_session_factory", lambda: factory)

    compat_decision = await receipts_mod.decide_before_submit(
        provider="bailian", run_id="c" * 32, model=MODEL
    )
    kit_decision = await decide_before_submit(
        provider="bailian", run_id="k" * 32, model=MODEL
    )
    assert (compat_decision.claim is not None) == (kit_decision.claim is not None)

    await receipts_mod.settle_submission(
        provider="bailian",
        run_id="c" * 32,
        status=STATUS_SUBMITTED,
        task_id="task-c",
        external_id="ext-c",
    )
    await settle_submission(
        provider="bailian",
        run_id="k" * 32,
        status=STATUS_SUBMITTED,
        task_id="task-k",
        external_id="ext-k",
    )

    assert (await receipts_mod.recover_external_id(
        provider="bailian", run_id="c" * 32, model=MODEL
    )) == "ext-c"
    assert (await recover_external_id(
        provider="bailian", run_id="k" * 32, model=MODEL
    )) == "ext-k"
