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

    测试库是 session 级的：一条用例把 `template_pack:read` 摘掉之后若不复原，
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
            await _set_role_codes(service, EDITOR, codes - {"template_pack:read"})
        await _write_snapshot(set(catalog.codes()))
        added = await _sync()
        async with factory()() as session:
            return await _role_codes(IdentityService(session), EDITOR), added

    codes, added = asyncio.run(scenario())
    assert "template_pack:read" not in codes, "管理员手工移除的权限被重启加回来了"
    assert added == {}


def test_newly_declared_permission_is_auto_added(restores_role_state) -> None:
    """快照里没有 `template_pack:read`（模拟上一版还没声明它）→ 自动补进 editor。"""
    from db.session import get_session_factory as factory

    async def scenario() -> tuple[set[str], dict[str, list[str]]]:
        async with factory()() as session:
            service = IdentityService(session)
            codes = await _role_codes(service, EDITOR)
            await _set_role_codes(service, EDITOR, codes - {"template_pack:read"})
        await _write_snapshot(set(catalog.codes()) - {"template_pack:read"})
        added = await _sync()
        async with factory()() as session:
            return await _role_codes(IdentityService(session), EDITOR), added

    codes, added = asyncio.run(scenario())
    assert "template_pack:read" in codes
    assert added.get(EDITOR) == ["template_pack:read"]


def test_snapshot_is_refreshed_after_sync(restores_role_state) -> None:
    """补完之后快照要跟上，否则每次启动都会把同一批权限重算一遍。"""
    from db.session import get_session_factory as factory

    async def scenario() -> object:
        await _write_snapshot(set(catalog.codes()) - {"template_pack:read"})
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
            await _set_role_codes(service, EDITOR, codes - {"template_pack:read"})
            await session.execute(delete(AppMeta).where(AppMeta.key == PERMISSION_SNAPSHOT_KEY))
            await session.commit()
        await _sync()
        async with factory()() as session:
            snapshot = await get_json(session, PERMISSION_SNAPSHOT_KEY)
            return await _role_codes(IdentityService(session), EDITOR), snapshot

    codes, snapshot = asyncio.run(scenario())
    assert "template_pack:read" in codes
    assert set(snapshot) == set(catalog.codes())


def test_unknown_permission_does_not_break_sync(restores_role_state) -> None:
    """快照里有一个当前目录不再声明的权限码：同步照常完成，**但不会被自动抹掉**。

    「自动路径只增不减」是刻意的：从数据上分不清「这一版删掉了这个权限点」和
    「写的人是个旧版本实例」。两种错的代价不对称——多留一个历史权限码只是少补
    一次，少留一个会把管理员手工移除的权限加回来。真要收缩，人跑
    `app sync-roles --prune-snapshot`（见 `test_prune_snapshot_can_shrink`）。
    """
    from db.session import get_session_factory as factory

    async def scenario() -> object:
        await _write_snapshot((set(catalog.codes()) | {"demo:ghost"}) - {"template_pack:read"})
        await _sync()
        async with factory()() as session:
            return await get_json(session, PERMISSION_SNAPSHOT_KEY)

    snapshot = set(asyncio.run(scenario()))
    assert "demo:ghost" in snapshot, "自动路径把快照改小了"
    assert snapshot - {"demo:ghost"} == set(catalog.codes())


def test_old_instance_cannot_shrink_snapshot(restores_role_state, monkeypatch) -> None:
    """滚动发布：旧版本实例（目录更小）不能把新实例写的快照改回去。

    这是 T33 的原样复现。旧实例 `declared` 只是新实例的子集，写入目标是
    `declared ∪ previous`，于是等于 `previous`——**根本不写**。
    没有这一条，旧实例最后写就会把快照退回旧集合，下次启动把少掉的几个当成
    「新增」补进内置角色，管理员手工摘掉的权限被悄悄加回来。
    """
    from modules.identity import service as service_module

    wide = set(catalog.codes())
    narrow = wide - {"template_pack:read", "template_pack:publish"}
    assert narrow < wide

    class _StaleCatalog:
        """旧版本进程眼里的权限目录。"""

        @staticmethod
        def codes() -> set[str]:
            return set(narrow)

    async def scenario() -> object:
        await _write_snapshot(wide)
        monkeypatch.setattr(service_module, "catalog", _StaleCatalog())
        await _sync()
        async with get_session_factory()() as session:
            return await get_json(session, PERMISSION_SNAPSHOT_KEY)

    snapshot = asyncio.run(scenario())
    assert set(snapshot) == wide, "旧版本实例把快照改小了"


def test_conflict_makes_it_recompute_instead_of_overwrite(restores_role_state) -> None:
    """CAS 撞车后必须重读重算：别人的写入不能被盖掉，差集要按新快照重算。

    `flaky` 在第一次写快照前模拟「另一个实例刚提交了一份旧快照」。
    正确的行为是：写入失败 → 重读 → 用新快照重算差集 → 再写一次（并集不丢东西）。
    """
    from modules.identity import service as service_module

    real_set = service_module.set_json
    rounds: list[list[str]] = []

    async def flaky(session, key, value, **kwargs):  # type: ignore[no-untyped-def]
        if not rounds:
            rounds.append(list(value))
            # 模拟另一个实例在「我算完」与「我写」之间改了快照
            await real_set(session, key, ["concurrent:only"], expected_version=None)
            await session.commit()
        return await real_set(session, key, value, **kwargs)

    async def scenario() -> object:
        await _write_snapshot(set(catalog.codes()) - {"template_pack:read"})
        rounds.clear()
        service_module.set_json = flaky
        try:
            await _sync()
        finally:
            service_module.set_json = real_set  # type: ignore[assignment]
        async with get_session_factory()() as session:
            return await get_json(session, PERMISSION_SNAPSHOT_KEY)

    snapshot = asyncio.run(scenario())
    assert len(rounds) == 1
    assert set(snapshot) == set(catalog.codes()) | {"concurrent:only"}, (
        "冲突后没有重读重算：别人的写入被盖掉了"
    )
    assert "template_pack:read" in snapshot, "重算的差集没把新增权限补进去"


def test_prune_snapshot_can_shrink(restores_role_state) -> None:
    """显式收缩：`prune=True` 把快照改写成「当前声明的全部」。

    自动路径只增不减是安全默认值，代价是历史权限码会一直留着；
    人明确知道「这些权限点这一版确实删了」时用这个开关收掉。
    """
    from db.session import get_session_factory as factory
    from modules.identity.service import IdentityService

    async def scenario() -> object:
        await _write_snapshot(set(catalog.codes()) | {"demo:ghost"})
        async with factory()() as session:
            await IdentityService(session).sync_new_permissions(prune=True)
        async with factory()() as session:
            return await get_json(session, PERMISSION_SNAPSHOT_KEY)

    assert set(asyncio.run(scenario())) == set(catalog.codes())


def test_dry_run_reports_without_writing(restores_role_state) -> None:
    """`app sync-roles --dry-run` 必须只算不写：升级前预演靠它。"""
    from db.session import get_session_factory as factory

    async def scenario() -> tuple[dict[str, list[str]], set[str]]:
        async with factory()() as session:
            service = IdentityService(session)
            codes = await _role_codes(service, EDITOR)
            await _set_role_codes(service, EDITOR, codes - {"template_pack:read"})
        async with factory()() as session:
            planned = await IdentityService(session).sync_builtin_role_permissions(
                dry_run=True
            )
        async with factory()() as session:
            return planned, await _role_codes(IdentityService(session), EDITOR)

    planned, codes = asyncio.run(scenario())
    assert planned.get(EDITOR) == ["template_pack:read"]
    assert "template_pack:read" not in codes, "dry-run 写库了"


def test_empty_catalog_refuses_to_write_snapshot(restores_role_state, monkeypatch) -> None:
    """目录为空时**什么都不写**。

    这个状态只有一种解释：模块没被发现（在脚本里直接 new 了服务，或者 CLI 忘了先
    `discover_modules()`）。此时写快照等于声称「这一版一个权限点都没声明」——
    下一次启动会把管理员手工移除的权限全当成「新增」补回来，`--prune-snapshot`
    更会把整张表抹成 `[]`（真踩过：CLI 里 catalog 是空集，prune 抹掉了 29 条）。
    """
    from modules.identity import service as service_module

    class _EmptyCatalog:
        @staticmethod
        def codes() -> set[str]:
            return set()

    async def scenario() -> object:
        await _write_snapshot(set(catalog.codes()))
        monkeypatch.setattr(service_module, "catalog", _EmptyCatalog())
        await _sync()
        async with get_session_factory()() as session:
            return await get_json(session, PERMISSION_SNAPSHOT_KEY)

    snapshot = asyncio.run(scenario())
    assert set(snapshot) == set(catalog.codes()), "目录为空时把快照写掉了"
