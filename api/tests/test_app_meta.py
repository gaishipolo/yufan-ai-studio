"""`app_meta` 的并发语义与审计。

这张表存「上一次运行时系统认定的事实」，第一个消费方是权限目录快照。
它原来只有 `key` + `value`，语义是最后写入者胜——**并发写的不一定是同一份判断**。
真实故障长这样：滚动发布，旧版本实例（只知道 29 个权限点）与新版本实例（31 个）
同时启动，旧实例最后写，快照退回 29；下一次启动把少掉的 2 个当成「新增」补进
内置角色，管理员手工摘掉的权限被悄悄加回来。全程没有任何报错。

下面几条用例钉住修法：版本号（CAS 凭据）、`StaleDataError`（并发写不覆盖）、
审计轨迹（谁在第几版改的）。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from db import app_meta
from db.app_meta import (
    AppMeta,
    current_actor,
    get_json,
    history,
    merge_json,
    read_entry,
    set_json,
)
from db.session import get_session_factory


def _key() -> str:
    return f"test.{uuid.uuid4().hex}"


@pytest.fixture()
def guard_history(monkeypatch):
    """把审计保留条数调小，不然要写 50 次才能看到裁剪。"""
    monkeypatch.setattr(app_meta, "HISTORY_KEEP", 3)
    yield 3


def test_write_bumps_version_and_stamps_actor() -> None:
    """写入要留下「第几版」和「谁写的」：排查的第一眼全靠这两个字段。"""
    key = _key()

    async def scenario() -> tuple[object, object]:
        factory = get_session_factory()
        async with factory() as session:
            assert await set_json(session, key, {"a": 1}, detail="初版")
            await session.commit()
        async with factory() as session:
            first = await read_entry(session, key)
        async with factory() as session:
            assert await set_json(session, key, {"a": 2}, expected_version=first.version)
            await session.commit()
        async with factory() as session:
            return first, await read_entry(session, key)

    first, second = asyncio.run(scenario())
    assert first.version == 1 and first.value == {"a": 1}
    assert second.version == 2 and second.value == {"a": 2}
    assert second.updated_by == current_actor()


def test_expected_version_zero_only_inserts() -> None:
    """`expected_version=0` = 只许新建。已存在时不能覆盖，否则「首次初始化」会重跑。"""
    key = _key()

    async def scenario() -> tuple[bool, object]:
        factory = get_session_factory()
        async with factory() as session:
            assert await set_json(session, key, ["first"], expected_version=0)
            await session.commit()
        async with factory() as session:
            again = await set_json(session, key, ["second"], expected_version=0)
            await session.commit()
        async with factory() as session:
            return again, await get_json(session, key)

    again, value = asyncio.run(scenario())
    assert again is False
    assert value == ["first"], "「只许新建」的写入把已有值覆盖了"


def test_write_against_stale_version_is_refused() -> None:
    """按旧版本决策的写入必须失败——这正是滚动发布时快照被改小的入口。

    模拟：读到 v1 → 另一个实例写成 v2 → 我拿 v1 去写。第三种情况如果不能失败，
    旧实例就能把快照退回旧集合。
    """
    key = _key()

    async def scenario() -> tuple[bool, object]:
        factory = get_session_factory()
        async with factory() as session:
            assert await set_json(session, key, ["old-instance"], expected_version=0)
            await session.commit()
        async with factory() as session:
            stale = await read_entry(session, key)
        async with factory() as session:  # 新版本实例
            assert await set_json(
                session, key, ["old-instance", "new-only"], expected_version=stale.version
            )
            await session.commit()
        async with factory() as session:  # 旧版本实例，拿着过期版本号
            accepted = await set_json(
                session, key, ["old-instance"], expected_version=stale.version
            )
            await session.commit()
        async with factory() as session:
            return accepted, await read_entry(session, key)

    accepted, entry = asyncio.run(scenario())
    assert accepted is False, "过期版本号的写入被接受了：快照可以被旧实例改小"
    assert entry.value == ["old-instance", "new-only"]
    assert entry.version == 2


def test_every_write_leaves_a_history_row() -> None:
    """审计与主写入同事务：一次写入一条，带人写的摘要。"""
    key = _key()

    async def scenario() -> list[object]:
        factory = get_session_factory()
        async with factory() as session:
            await set_json(session, key, [1], detail="29→30 个权限码；新增 x")
            await set_json(session, key, [1, 2], detail="30→31 个权限码；新增 y")
            await session.commit()
        async with factory() as session:
            return list(await history(session, key))

    rows = asyncio.run(scenario())
    assert [row.version for row in rows] == [2, 1], "轨迹必须按时间倒序"
    assert rows[0].detail == "30→31 个权限码；新增 y"
    assert rows[0].actor == current_actor()


def test_history_is_bounded(guard_history: int) -> None:
    """一个键只留最近若干条：它是状态表的历史，不是业务日志。"""
    key = _key()

    async def scenario() -> list[object]:
        factory = get_session_factory()
        async with factory() as session:
            for i in range(guard_history + 2):
                await set_json(session, key, [i])
            await session.commit()
        async with factory() as session:
            return list(await history(session, key, limit=100))

    rows = asyncio.run(scenario())
    assert len(rows) == guard_history
    assert [row.version for row in rows] == [5, 4, 3]


def test_rolled_back_write_leaves_no_history() -> None:
    """主写入回滚，审计行也要跟着消失——否则留下「改过但没改成」的记录。"""
    key = _key()

    async def scenario() -> list[object]:
        factory = get_session_factory()
        async with factory() as session:
            await set_json(session, key, ["x"], expected_version=0)
            await session.rollback()
        async with factory() as session:
            return list(await history(session, key)) + [await get_json(session, key)]

    rows = asyncio.run(scenario())
    assert rows == [None], f"回滚后还留着审计/值：{rows}"


def test_merge_retries_and_keeps_the_other_write(monkeypatch) -> None:
    """`merge_json` 冲突后要用**新值**重算，而不是把别人的写入盖掉。

    `flaky` 在第一次写入前先模拟另一个实例提交一份改动，制造 CAS 失败。
    """
    key = _key()
    seen: list[object] = []
    real_set = app_meta.set_json
    interfered = False

    async def flaky(session, name, value, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal interfered
        if not interfered and name == key:
            interfered = True
            await real_set(session, key, ["other"], expected_version=None)
            await session.commit()
        return await real_set(session, name, value, **kwargs)

    def updater(old: object) -> list[str]:
        seen.append(old)
        return sorted(set(old or []) | {"mine"})

    monkeypatch.setattr(app_meta, "set_json", flaky)

    async def scenario() -> tuple[object, bool]:
        factory = get_session_factory()
        async with factory() as session:
            value, written = await merge_json(session, key, updater)
            await session.commit()
            return value, written

    value, written = asyncio.run(scenario())
    assert written is True
    assert len(seen) == 2, "冲突后没有重读重算"
    assert seen[1] == ["other"], "第二轮 updater 没拿到别人刚写的值"
    assert value == ["mine", "other"], "别人的写入被盖掉了"


def test_merge_skips_write_when_value_is_unchanged() -> None:
    """算出来跟原来一样就别写：每次启动都凭空多一个版本号只会让轨迹变噪声。"""
    key = _key()

    async def scenario() -> tuple[bool, int]:
        factory = get_session_factory()
        async with factory() as session:
            await set_json(session, key, ["same"], expected_version=0)
            await session.commit()
        async with factory() as session:
            _value, written = await merge_json(session, key, lambda old: list(old or []))
            await session.commit()
        async with factory() as session:
            entry = await read_entry(session, key)
            return written, entry.version

    written, version = asyncio.run(scenario())
    assert written is False
    assert version == 1


def test_app_meta_row_exposes_version_column() -> None:
    """`version` 必须是真实的列（而不只是 dataclass 字段）：迁移里加了它。"""
    assert "version" in AppMeta.__table__.columns
    assert AppMeta.__table__.columns["version"].nullable is False
