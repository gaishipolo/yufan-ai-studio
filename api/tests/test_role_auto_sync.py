"""权限点自动并入内置角色。

背景：上线一个带新权限点的模块后，老库里的 `editor` 仍是旧权限集。超管拿 `*`
全程自测发现不了，非超管点新功能的按钮直接 403。过去靠「发版时记得跑
`app sync-roles`」——这种靠人记的动作迟早会漏（上一个模块就漏了）。

现在的语义是「**只补新增**」：新声明的权限码自动并入内置角色；管理员手工移除的
旧权限**不会**被重启加回来。两者的区别靠 `app_meta.permissions.catalog` 里
上一版目录快照来判定。下面几条用例把两种情形都钉住。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete

from core.permissions import catalog
from db.app_meta import AppMeta, get_json, set_json
from db.session import get_session_factory
from modules.identity.service import PERMISSION_SNAPSHOT_KEY, IdentityService

EDITOR = "editor"


async def _role_codes(service: IdentityService, code: str) -> set[str]:
    role = await service.roles.get_by_code(code)
    assert role is not None, f"内置角色 {code} 不存在"
    return set((await service.roles.permissions_for([role.id])).get(role.id, []))


async def _set_role_codes(service: IdentityService, code: str, codes: set[str]) -> None:
    role = await service.roles.get_by_code(code)
    assert role is not None
    await service.roles.set_permissions(role.id, sorted(codes))
    await service.session.commit()


@pytest.fixture
def restores_role_state():
    """备份并恢复「快照 + editor 权限」，避免用例之间互相污染。

    测试库是 session 级的：一条用例把 `template:use` 摘掉之后若不复原，
    后面任何「非超管能不能用模板」的断言都会莫名其妙地 403。
    """
    from db.session import get_session_factory as factory

    async def snapshot() -> tuple[object, set[str]]:
        async with factory()() as session:
            meta = await get_json(session, PERMISSION_SNAPSHOT_KEY)
            codes = await _role_codes(IdentityService(session), EDITOR)
            return meta, codes

    original_meta, original_codes = asyncio.run(snapshot())

    async def restore() -> None:
        async with factory()() as session:
            if original_meta is None:
                await session.execute(
                    delete(AppMeta).where(AppMeta.key == PERMISSION_SNAPSHOT_KEY)
                )
            else:
                await set_json(session, PERMISSION_SNAPSHOT_KEY, original_meta)
            await _set_role_codes(IdentityService(session), EDITOR, original_codes)
            await session.commit()

    yield
    asyncio.run(restore())


async def _write_snapshot(codes: set[str]) -> None:
    async with get_session_factory()() as session:
        await set_json(session, PERMISSION_SNAPSHOT_KEY, sorted(codes))
        await session.commit()


async def _sync() -> dict[str, list[str]]:
    async with get_session_factory()() as session:
        return await IdentityService(session).sync_new_permissions()


def test_startup_writes_catalog_snapshot(client) -> None:
    """启动（`bootstrap`）后必须留下快照，否则「只补新增」永远退化成全量补齐。"""
    from db.session import get_session_factory as factory

    async def read() -> object:
        async with factory()() as session:
            return await get_json(session, PERMISSION_SNAPSHOT_KEY)

    snapshot = asyncio.run(read())
    assert snapshot is not None
    assert set(snapshot) == set(catalog.codes())


def test_admin_removed_permission_is_not_added_back(restores_role_state) -> None:
    """快照里已有全部权限 → 管理员摘掉的旧权限不该被下次启动塞回来。"""
    from db.session import get_session_factory as factory

    async def scenario() -> tuple[set[str], dict[str, list[str]]]:
        async with factory()() as session:
            service = IdentityService(session)
            codes = await _role_codes(service, EDITOR)
            await _set_role_codes(service, EDITOR, codes - {"template:use"})
        await _write_snapshot(set(catalog.codes()))
        added = await _sync()
        async with factory()() as session:
            return await _role_codes(IdentityService(session), EDITOR), added

    codes, added = asyncio.run(scenario())
    assert "template:use" not in codes, "管理员手工移除的权限被重启加回来了"
    assert added == {}


def test_newly_declared_permission_is_auto_added(restores_role_state) -> None:
    """快照里没有 `template:use`（模拟上一版还没声明它）→ 自动补进 editor。"""
    from db.session import get_session_factory as factory

    async def scenario() -> tuple[set[str], dict[str, list[str]]]:
        async with factory()() as session:
            service = IdentityService(session)
            codes = await _role_codes(service, EDITOR)
            await _set_role_codes(service, EDITOR, codes - {"template:use"})
        await _write_snapshot(set(catalog.codes()) - {"template:use"})
        added = await _sync()
        async with factory()() as session:
            return await _role_codes(IdentityService(session), EDITOR), added

    codes, added = asyncio.run(scenario())
    assert "template:use" in codes
    assert added.get(EDITOR) == ["template:use"]


def test_snapshot_is_refreshed_after_sync(restores_role_state) -> None:
    """补完之后快照要跟上，否则每次启动都会把同一批权限重算一遍。"""
    from db.session import get_session_factory as factory

    async def scenario() -> object:
        await _write_snapshot(set(catalog.codes()) - {"template:use"})
        await _sync()
        async with factory()() as session:
            return await get_json(session, PERMISSION_SNAPSHOT_KEY)

    assert set(asyncio.run(scenario())) == set(catalog.codes())


def test_first_run_backfills_whole_spec(restores_role_state) -> None:
    """没有快照 = 老库第一次升到这一版：做一次全量补齐，把历史欠账还上。"""
    from db.session import get_session_factory as factory

    async def scenario() -> tuple[set[str], object]:
        async with factory()() as session:
            service = IdentityService(session)
            codes = await _role_codes(service, EDITOR)
            await _set_role_codes(service, EDITOR, codes - {"template:use", "template:review"})
            await session.execute(delete(AppMeta).where(AppMeta.key == PERMISSION_SNAPSHOT_KEY))
            await session.commit()
        await _sync()
        async with factory()() as session:
            snapshot = await get_json(session, PERMISSION_SNAPSHOT_KEY)
            return await _role_codes(IdentityService(session), EDITOR), snapshot

    codes, snapshot = asyncio.run(scenario())
    assert {"template:use", "template:review"} <= codes
    assert set(snapshot) == set(catalog.codes())


def test_unknown_permission_does_not_break_sync(restores_role_state) -> None:
    """目录里出现一个没有任何内置角色声明的权限点时，同步照常完成。

    这正是真实场景：新模块加了 `voice:create`，而 `BUILTIN_ROLES` 里没人提它。
    该做的事是「什么都不补，但把快照更新掉」，不是报错。
    """
    from db.session import get_session_factory as factory

    async def scenario() -> object:
        await _write_snapshot((set(catalog.codes()) | {"demo:ghost"}) - {"template:use"})
        await _sync()
        async with factory()() as session:
            return await get_json(session, PERMISSION_SNAPSHOT_KEY)

    snapshot = set(asyncio.run(scenario()))
    assert "demo:ghost" not in snapshot
    assert snapshot == set(catalog.codes())


def test_dry_run_reports_without_writing(restores_role_state) -> None:
    """`app sync-roles --dry-run` 必须只算不写：升级前预演靠它。"""
    from db.session import get_session_factory as factory

    async def scenario() -> tuple[dict[str, list[str]], set[str]]:
        async with factory()() as session:
            service = IdentityService(session)
            codes = await _role_codes(service, EDITOR)
            await _set_role_codes(service, EDITOR, codes - {"template:use"})
        async with factory()() as session:
            planned = await IdentityService(session).sync_builtin_role_permissions(
                dry_run=True
            )
        async with factory()() as session:
            return planned, await _role_codes(IdentityService(session), EDITOR)

    planned, codes = asyncio.run(scenario())
    assert planned.get(EDITOR) == ["template:use"]
    assert "template:use" not in codes, "dry-run 写库了"
