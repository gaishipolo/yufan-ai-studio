"""提交台账（kit/submission_guard）：状态机语义 + 同步/异步两种结算形态。

台账不是百炼的私有实现，而是所有供应商驱动共用的提交治理件 —— 所以这里测的
是**语义**（幂等、禁止自动重提、两种结算形态），不是「某张表能读写」。

对照：`tests/test_submission_receipts.py` 走的是**兼容层**入口（`receipts.py`），
本文件走 **kit 直连**入口。两边指向同一份实现 → 最后一条测试专门钉住
「两条入口行为一致」，防止有人只改一边。
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import modules.model_driver.kit.submission_guard as guard_mod
import modules.model_driver.receipts as receipts_mod
from core.exceptions import BadRequest, UpstreamError
from db.base import Base
from infra.delivery import possibly_delivered
from modules.model_driver import models as driver_models  # noqa: F401 — 注册表副作用
from modules.model_driver.kit.submission_guard import (
    STATUS_REJECTED,
    STATUS_SUBMITTED,
    STATUS_UNKNOWN,
    ReceiptConflict,
    decide_before_submit,
    get_receipt,
    guarded_submit,
    recover_external_id,
    settle_failure,
    settle_submission,
)
from modules.model_driver.models import SubmissionReceipt

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


# ---------------------------------------------------------------------------
# `guarded_submit`：各驱动接入台账的入口
# ---------------------------------------------------------------------------


async def test_guarded_submit_records_success_with_attempt_key(
    session: AsyncSession,
) -> None:
    """成功提交 → 台账一行：轮次键 + 真实通道 + 上游标识。"""
    record_id = "rec-1"

    async def call() -> str:
        return "task-42"

    result = await guarded_submit(
        provider="agnes",
        record_id=record_id,
        model=MODEL,
        channel_used="base64",
        call=call,
    )
    assert result == "task-42"

    receipt = await get_receipt(session, provider="agnes", run_id=f"{record_id}#1")
    assert receipt is not None
    assert receipt.record_id == record_id
    assert receipt.status == STATUS_SUBMITTED
    assert receipt.task_id == "task-42"
    assert receipt.external_id == "task-42"
    assert receipt.channel_used == "base64"


async def test_guarded_submit_records_rejected_on_4xx(session: AsyncSession) -> None:
    """4xx：上游明确拒绝、没受理 → 记 `rejected`（可安全重试），异常照抛。"""
    record_id = "rec-2"

    async def call() -> str:
        raise BadRequest("参数错")

    with pytest.raises(BadRequest):
        await guarded_submit(provider="agnes", record_id=record_id, model=MODEL, call=call)

    receipt = await get_receipt(session, provider="agnes", run_id=f"{record_id}#1")
    assert receipt is not None
    assert receipt.status == STATUS_REJECTED
    assert receipt.error is not None
    assert "参数错" in receipt.error


async def test_guarded_submit_records_unknown_on_5xx(session: AsyncSession) -> None:
    """5xx：可能已受理 → 记 `submission_unknown`（禁止自动重提）。"""
    record_id = "rec-3"

    async def call() -> str:
        raise UpstreamError("上游 502")

    with pytest.raises(UpstreamError):
        await guarded_submit(provider="agnes", record_id=record_id, model=MODEL, call=call)

    receipt = await get_receipt(session, provider="agnes", run_id=f"{record_id}#1")
    assert receipt is not None
    assert receipt.status == STATUS_UNKNOWN


async def test_guarded_submit_uses_the_same_verdict_as_the_framework(
    session: AsyncSession,
) -> None:
    """台账的三态判定必须与框架**同一份**判定（`infra/delivery.py`）。

    两边判定不一致的后果很具体：框架判「可安全重试」而台账判「可能已受理」时，
    人会同时看到「任务失败」与「可能已计费」两句矛盾的话。
    """
    cases = [
        (BadRequest("4xx"), STATUS_REJECTED),
        (UpstreamError("5xx"), STATUS_UNKNOWN),
        (httpx.ConnectTimeout("连不上"), STATUS_REJECTED),
        (httpx.ReadTimeout("读超时"), STATUS_UNKNOWN),
    ]
    for index, (exc, expected) in enumerate(cases):
        record_id = f"rec-verdict-{index}"

        async def call(exc: Exception = exc) -> str:
            raise exc

        with pytest.raises(type(exc)):
            await guarded_submit(provider="agnes", record_id=record_id, model=MODEL, call=call)

        receipt = await get_receipt(session, provider="agnes", run_id=f"{record_id}#1")
        assert receipt is not None
        assert receipt.status == expected
        # 台账的判定 = 框架的判定（同一份实现，不是抄一遍）
        assert (receipt.status == STATUS_UNKNOWN) == possibly_delivered(exc)


async def test_guarded_submit_starts_a_new_attempt_each_time(
    session: AsyncSession,
) -> None:
    """同一记录多次提交 → 每轮一行（轮次键递增），历史留着可查。"""
    record_id = "rec-4"

    async def call() -> str:
        return "task-x"

    await guarded_submit(provider="agnes", record_id=record_id, model=MODEL, call=call)
    await guarded_submit(provider="agnes", record_id=record_id, model=MODEL, call=call)

    rows = (await session.execute(select(SubmissionReceipt))).scalars().all()
    assert sorted(row.run_id for row in rows) == [f"{record_id}#1", f"{record_id}#2"]


async def test_guarded_submit_does_not_block_manual_resubmit(
    session: AsyncSession,
) -> None:
    """人工重提（复用同一 record id）不被旧行挡住，也不会拿回上一轮的结果。

    这正是「轮次键」存在的理由：若拿 record id 直接当键，人工重提会被上一轮的
    台账挡住 → 用户拿回上一轮的图。
    """
    record_id = "rec-5"

    async def failing() -> str:
        raise BadRequest("第一次没过")

    with pytest.raises(BadRequest):
        await guarded_submit(provider="agnes", record_id=record_id, model=MODEL, call=failing)

    async def succeeding() -> str:
        return "task-second"

    result = await guarded_submit(
        provider="agnes", record_id=record_id, model=MODEL, call=succeeding
    )
    assert result == "task-second"

    receipt = await get_receipt(session, provider="agnes", run_id=f"{record_id}#2")
    assert receipt is not None
    assert receipt.status == STATUS_SUBMITTED
    assert receipt.external_id == "task-second"


async def test_guarded_submit_without_record_id_skips_the_ledger(
    session: AsyncSession,
) -> None:
    """没有业务记录 id → 入不了账（台账的键就是它）。

    这时**不该拦停业务**：台账是账本，不是门禁 → 直接提交，只是不记。
    """
    async def call() -> str:
        return "task-noid"

    result = await guarded_submit(provider="agnes", record_id="", model=MODEL, call=call)
    assert result == "task-noid"

    rows = (await session.execute(select(SubmissionReceipt))).scalars().all()
    assert rows == []


async def test_guarded_submit_marks_unknown_when_no_identifier(
    session: AsyncSession,
) -> None:
    """提交成功却没拿到可续接的标识 → 记「可能已受理」。

    不能记「已提交」（缺凭证的行等于没提交过），也不能记「已拒绝」（上游确实
    受理了）→ 只能记成待人工核对。
    """
    record_id = "rec-6"

    async def call() -> None:
        return None

    await guarded_submit(provider="agnes", record_id=record_id, model=MODEL, call=call)

    receipt = await get_receipt(session, provider="agnes", run_id=f"{record_id}#1")
    assert receipt is not None
    assert receipt.status == STATUS_UNKNOWN


# ---------------------------------------------------------------------------
# `record_id` 与 `settle_failure`：给「自己编排提交顺序」的驱动用
# ---------------------------------------------------------------------------


async def test_decide_before_submit_records_the_business_record_id(
    session: AsyncSession,
) -> None:
    """台账行要带**业务记录 id** —— 崩溃恢复器靠它把凭证找回来。

    键（`run_id`）与业务记录 id 是**两回事**：百炼的键是幂等键，轮次键的驱动用
    `{record_id}#{轮次}`。少了 `record_id` 这一列，恢复器查不到任何凭证 →
    崩溃后上游已经受理的任务白丢。
    """
    record_id = "rec-biz-1"

    await decide_before_submit(
        provider="bailian", run_id=RUN_ID, model=MODEL, record_id=record_id
    )

    receipt = await get_receipt(session, provider="bailian", run_id=RUN_ID)
    assert receipt is not None
    assert receipt.record_id == record_id
    assert receipt.run_id == RUN_ID, "键与业务记录 id 是两回事，不能互相顶替"


async def test_settle_failure_uses_the_same_verdict_as_the_framework(
    session: AsyncSession,
) -> None:
    """`settle_failure` 与 `guarded_submit`、与框架 runner 用**同一份**三态判定。

    它是给**自己编排提交顺序**的驱动准备的出口（百炼：本地校验与素材上传必须
    在抢占之前），所以判得必须一模一样 —— 否则会出现「台账说可重试、框架说
    禁止重提」这种两边打架的情况，人会同时看到两句矛盾的话。
    """
    cases = [
        (BadRequest("4xx"), STATUS_REJECTED),
        (UpstreamError("5xx"), STATUS_UNKNOWN),
        (httpx.ReadTimeout("读超时"), STATUS_UNKNOWN),
        (httpx.ConnectTimeout("连不上"), STATUS_REJECTED),
    ]
    for index, (exc, expected) in enumerate(cases):
        run_id = f"f{index}" + "x" * 31
        await decide_before_submit(provider="bailian", run_id=run_id, model=MODEL)

        status = await settle_failure(provider="bailian", run_id=run_id, exc=exc)
        assert status == expected

        receipt = await get_receipt(session, provider="bailian", run_id=run_id)
        assert receipt is not None
        assert receipt.status == expected
        # 台账的判定 = 框架的判定（同一份实现，不是抄一遍）
        assert (receipt.status == STATUS_UNKNOWN) == possibly_delivered(exc)


async def test_settle_failure_does_not_mask_the_original_error(
    session: AsyncSession,
) -> None:
    """台账写不进去**不能掩盖原始异常** —— 上游的错误信息更重要。

    这里让结算必然失败（这一轮根本没有回执行）→ `settle_failure` 仍要把判定
    结果交回去，而且**不能抛**（抛了就把上游的真实错误顶掉了）。
    """
    status = await settle_failure(
        provider="bailian", run_id="g" * 32, exc=BadRequest("上游说：参数错")
    )
    assert status == STATUS_REJECTED, "落库失败也要把判定结果交回去"
