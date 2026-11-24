"""4 个驱动的**台账接入**验证。

## 为什么单独一个文件

台账本身的行为（轮次键、三态、判定口径）在 `tests/test_submission_guard.py`
里按 kit 入口测过了；这里测的是**接线** —— 驱动的 `submit` 有没有真的经过
`guarded_submit`，以及 provider / 业务记录 id / 通道有没有被正确带进去。

接线最容易出的错不是「没接」，而是「接了但传错了」：

- provider 写成了别的供应商名 → 账本上查不到这家；
- `record_id` 传成空 → kit 会**跳过台账**（不拦停业务），账本上什么都没有，
  而人以为「记上了」；
- 通道没带 → `channel_used` 落空串，看着像「这次没素材」。

所以这里不测 HTTP（各驱动自己的测试文件已经测了），只测驱动到台账这一段。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import modules.model_driver.kit.submission_guard as guard_mod
from db.base import Base
from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
)
from modules.model_driver import models as driver_models  # noqa: F401 — 注册表副作用
from modules.model_driver.drivers.agnes import AgnesDriver
from modules.model_driver.drivers.agnes_video import AgnesVideoDriver
from modules.model_driver.drivers.grsai import GrsaiImageDriver
from modules.model_driver.drivers.grsai_video import GrsaiVideoDriver
from modules.model_driver.kit.submission_guard import STATUS_SUBMITTED
from modules.model_driver.models import SubmissionReceipt

MODEL = "some-model"


@pytest.fixture
async def session(monkeypatch: pytest.MonkeyPatch) -> AsyncSession:
    """每测试一张干净的 sqlite 内存库（kit 入口）。

    kit 自己开 session（驱动拿不到 runner 的 session），所以把 **kit 模块**的
    factory 换成指向同一张内存库 —— 否则断言全落空，还会写到开发库。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(guard_mod, "get_session_factory", lambda: factory)
    async with factory() as db:
        yield db
    await engine.dispose()


# (驱动类, 驱动提交成功后返回的东西)
#
# 返回形态按各驱动的真实语义给，而不是统一给一个字符串：同步生图（agnes）
# 返回终态快照（没有独立任务号），异步驱动返回上游任务号。台账要在这两种
# 形态下都留下**可续接的凭证**。
DRIVERS: list[tuple[type[Any], Any]] = [
    (AgnesDriver, GenerationProgress(external_id="task-agnes", state=GenerationState.SUCCEEDED)),
    (AgnesVideoDriver, "task-agnes-video"),
    (GrsaiImageDriver, "task-grsai"),
    (GrsaiVideoDriver, "task-grsai-video"),
]

DRIVER_IDS = [driver_cls.__name__ for driver_cls, _ in DRIVERS]


def _request(record_id: str, *, channel_used: str = "") -> GenerationRequest:
    return GenerationRequest(
        capability=Capability.IMAGE,
        model=MODEL,
        prompt="画一只猫",
        request_id=record_id,
        channel_used=channel_used,
    )


@pytest.mark.parametrize(("driver_cls", "outcome"), DRIVERS, ids=DRIVER_IDS)
async def test_driver_submit_leaves_a_ledger_row(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    driver_cls: type[Any],
    outcome: Any,
) -> None:
    """驱动的 `submit` 必须经过台账：留下一行，键与凭证都对。"""
    record_id = f"rec-{driver_cls.__name__}"

    async def stub(self: Any, request: GenerationRequest) -> Any:
        return outcome

    monkeypatch.setattr(driver_cls, "_submit_once", stub)

    driver = driver_cls()
    await driver.submit(_request(record_id))

    rows = (await session.execute(select(SubmissionReceipt))).scalars().all()
    assert len(rows) == 1
    receipt = rows[0]
    # provider 必须等于驱动自己的名字 —— 台账是按供应商对账的
    assert receipt.provider == driver_cls.name
    # 轮次键：同一记录可以多次提交，每轮一行
    assert receipt.run_id == f"{record_id}#1"
    assert receipt.record_id == record_id
    assert receipt.status == STATUS_SUBMITTED
    assert receipt.model == MODEL
    # 两种结算形态都必须留下可续接的凭证
    assert receipt.external_id == getattr(outcome, "external_id", outcome)


@pytest.mark.parametrize(("driver_cls", "outcome"), DRIVERS, ids=DRIVER_IDS)
async def test_driver_submit_records_the_channel_it_actually_used(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    driver_cls: type[Any],
    outcome: Any,
) -> None:
    """台账要落**本次实际走的通道**（不是驱动声明支持的那几个）。"""
    record_id = f"rec-chan-{driver_cls.__name__}"

    async def stub(self: Any, request: GenerationRequest) -> Any:
        return outcome

    monkeypatch.setattr(driver_cls, "_submit_once", stub)

    driver = driver_cls()
    await driver.submit(_request(record_id, channel_used="base64,oss"))

    receipt = (
        await session.execute(
            select(SubmissionReceipt).where(SubmissionReceipt.record_id == record_id)
        )
    ).scalar_one()
    assert receipt.channel_used == "base64,oss"


@pytest.mark.parametrize(("driver_cls", "outcome"), DRIVERS, ids=DRIVER_IDS)
async def test_driver_submit_without_record_id_leaves_no_row(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    driver_cls: type[Any],
    outcome: Any,
) -> None:
    """没有业务记录 id → 台账入不了账，但**不能拦停业务**（提交照常完成）。

    这是刻意设计：台账是账本不是门禁。缺 `record_id` 只意味着这一笔记不进来。
    """
    async def stub(self: Any, request: GenerationRequest) -> Any:
        return outcome

    monkeypatch.setattr(driver_cls, "_submit_once", stub)

    driver = driver_cls()
    result = await driver.submit(_request(""))

    assert result is not None
    rows = (await session.execute(select(SubmissionReceipt))).scalars().all()
    assert rows == []
