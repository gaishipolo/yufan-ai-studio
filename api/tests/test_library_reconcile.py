"""并存观察期的对账（M5）：`reconcile_legacy` 与 `library reconcile`。

为什么单独守这一个函数：观察期两边都会有人写，而「新库列表看起来正常」这件事
和「旧库的增量都追平了」没有任何关系。对账要回答的是**三个方向的差集**，
每个方向的处置都不同：

- 旧库有、v2 没有、格式认得 → 跑 import 就能补；
- 旧库有、v2 没有、格式不认得 → import 会永远跳过，只能人工确认；
- v2 有、旧库已删 → 反向漂移，删不删由人决定。

三者混成一个数字（「差 N 条」）时，值班的人不知道该跑命令还是该叫人来，
所以这里逐条钉住分桶。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, or_

from modules.asset.models import Asset
from modules.library.import_archive import LEGACY_PREFIX, reconcile_legacy
from modules.library.models import LibraryAsset

#: 本文件插入的行统一用这个前缀，收尾时按前缀一次删干净。
#:
#: 为什么必须收尾：测试库是 session 级共享的，对账是**全表扫描**。留下几行
#: `legacy:rec-*` 会让后面跑的对账 / 导入用例看到凭空多出来的「已迁」条目，
#: 报出来的失败跟那次改动毫无关系。
_ID_PREFIX = "rec-"


def _run(coro_factory):
    async def run():
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            return await coro_factory(session)

    return asyncio.run(run())


def _add_legacy(asset_id: str, owner: str, *, storage_key: str, kind: str = "image") -> None:
    async def run(session):
        session.add(
            Asset(
                id=asset_id,
                name="旧素材.png",
                type=kind,
                mime_type="image/png",
                size_bytes=10,
                storage_key=storage_key,
                owner_id=owner,
                source="archive",
            )
        )
        await session.commit()

    _run(run)


def _add_migrated(legacy_id: str, owner: str) -> None:
    async def run(session):
        session.add(
            LibraryAsset(
                name="旧素材.png",
                kind="image",
                origin="imported",
                storage_key="library/blobs/aa/x.png",
                owner_id=owner,
                source_key=f"{LEGACY_PREFIX}{legacy_id}",
            )
        )
        await session.commit()

    _run(run)


def _reconcile(owner: str = ""):
    async def run(session):
        return await reconcile_legacy(session, owner=owner)

    return _run(run)


@pytest.fixture(autouse=True)
def _cleanup() -> None:
    yield

    async def run(session):
        await session.execute(
            delete(Asset).where(
                or_(Asset.id.like(f"{_ID_PREFIX}%"), Asset.owner_id.like("reconcile-%"))
            )
        )
        await session.execute(
            delete(LibraryAsset).where(
                LibraryAsset.source_key.like(f"{LEGACY_PREFIX}{_ID_PREFIX}%")
            )
        )
        await session.commit()

    _run(run)


def test_全部迁移完成后没有缺口():
    owner = "reconcile-ok"
    _add_legacy("rec-ok-1", owner, storage_key="a/one.png")
    _add_migrated("rec-ok-1", owner)

    report = _reconcile(owner)
    assert (report.legacy_total, report.migrated) == (1, 1)
    assert report.in_sync


def test_待补与格式不支持分开报():
    owner = "reconcile-gap"
    _add_legacy("rec-gap-1", owner, storage_key="a/one.png")
    _add_migrated("rec-gap-1", owner)
    # 能补：v2 里没有
    _add_legacy("rec-gap-2", owner, storage_key="a/two.png")
    # 永远补不了：早期归档行没有扩展名，导入脚本不猜格式
    _add_legacy("rec-gap-3", owner, storage_key="no-extension")

    report = _reconcile(owner)
    assert report.missing_supported == ["rec-gap-2"]
    assert report.missing_unsupported == ["rec-gap-3"]
    assert not report.in_sync

    lines = "\n".join(report.lines())
    assert "待补 1 条" in lines
    assert "格式不支持 1 条" in lines


def test_旧库已删_v2_还在要反向报出来():
    """旧行软删、v2 那行还在。观察期最容易漏的方向：没人会去旧库点删除后
    再到 v2 里核对，而这条 v2 行可能已经被共享出去了。"""
    owner = "reconcile-orphan"

    async def soft_delete(session):
        session.add(
            Asset(
                id="rec-orphan-1",
                name="旧素材.png",
                type="image",
                mime_type="image/png",
                size_bytes=10,
                storage_key="a/gone.png",
                owner_id=owner,
                source="archive",
                deleted_at=datetime.now(UTC),
            )
        )
        await session.commit()

    _run(soft_delete)
    _add_migrated("rec-orphan-1", owner)

    # 孤儿不按 owner 归属（旧行已经没了，无法判断属于谁），所以不带 owner 查。
    # 断言用「包含」而不是相等：测试库是会话级共享的，别处留下的条目也会出现在
    # 报告里；这里只关心这一条被归到了哪个桶。
    report = _reconcile()
    assert "rec-orphan-1" in report.orphan
    assert "rec-orphan-1" not in [*report.missing_supported, *report.missing_unsupported]
    assert "v2 独有" in "\n".join(report.lines())


def test_owner_过滤只算这个人的条目():
    _add_legacy("rec-mine-1", "reconcile-a", storage_key="a/mine.png")
    _add_legacy("rec-theirs-1", "reconcile-b", storage_key="a/theirs.png")

    mine = _reconcile("reconcile-a")
    assert mine.legacy_total == 1
    assert mine.missing_supported == ["rec-mine-1"]


def test_每日对账任务已注册且给的是同一份结论():
    """周期任务不是「另一个算法」，它就是 reconcile 的一层日志包装。

    这条用例把两件事钉在一起：任务真的注册了（漏了 `ModuleSpec.periodic`
    会启动就失败，但漏了 import 也会静默不跑），以及它报的结论跟命令行一致
    ——两边各写一遍判定，观察期就会出现「CLI 说没问题、日志天天告警」。
    """
    from core.scheduler import periodic_jobs
    from modules.library.observation import run_legacy_sync

    _add_legacy("rec-daily-1", "reconcile-daily", storage_key="a/daily.png")

    report = asyncio.run(run_legacy_sync())
    assert report is not None
    assert "rec-daily-1" in report.missing_supported
    assert "library.legacy.sync" in {job.name for job in periodic_jobs()}
