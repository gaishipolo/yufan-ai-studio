"""个人-小组-团队三层权限授予。

这组用例守的是升级的核心不变量：**有效权限 = 四层并集，没有 deny**。
所以「个人永远 ≥ 小组/团队」不是一条需要特判的规则，而是集合运算的结果。

另外钉住两件容易在重构中悄悄坏掉的事：
- 授权改动**免重登**（`resolve_principal` 每请求查库，不是令牌快照）；
- 删组要清干净成员与 grant，否则会留下没人看得见却仍然生效的孤儿授权。

测试库是 session 级共享的，所以每个用例结束后把自己的组、成员、个人授权
与团队层改动全部还原（见 `_restore_layers`）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from db.session import get_session_factory
from modules.identity.models import GroupMember, PermissionGrant, User
from modules.identity.permissions import (
    SCOPE_GROUP,
    SCOPE_TEAM,
    SCOPE_USER,
    TEAM_BASE_PERMISSIONS,
    TEAM_SCOPE_ID,
)
from modules.identity.repository import LayerCodes, PermissionLayerRepository
from modules.identity.service import IdentityService

API = "/api/v1/identity"
VIEWER = ("viewer", "viewer@12345")


async def _with_service(fn):  # type: ignore[no-untyped-def]
    async with get_session_factory()() as session:
        return await fn(IdentityService(session))


async def _viewer_id(service: IdentityService) -> str:
    user = await service.users.get_by_username(VIEWER[0])
    assert isinstance(user, User)
    return user.id


@pytest.fixture(autouse=True)
def _restore_layers() -> Iterator[None]:
    """撤销本用例写进三层表的任何东西，避免污染后续用例。"""

    async def restore() -> None:
        async with get_session_factory()() as session:
            await session.execute(delete(GroupMember))
            await session.execute(
                delete(PermissionGrant).where(
                    PermissionGrant.scope.in_([SCOPE_GROUP, SCOPE_USER, SCOPE_TEAM])
                )
            )
            from modules.identity.models import Group

            await session.execute(delete(Group))
            session.add_all(
                PermissionGrant(
                    scope=SCOPE_TEAM, scope_id=TEAM_SCOPE_ID, permission=code
                )
                for code in TEAM_BASE_PERMISSIONS
            )
            await session.commit()

    yield
    asyncio.run(restore())


def _login(client: TestClient) -> dict[str, str]:
    resp = client.post(
        f"{API}/login", json={"username": VIEWER[0], "password": VIEWER[1]}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}


def test_team_base_permissions_are_seeded_once() -> None:
    async def check(service: IdentityService) -> None:
        codes = await service.layers.grants(SCOPE_TEAM, TEAM_SCOPE_ID)
        assert set(codes) == set(TEAM_BASE_PERMISSIONS)
        # 第二次启动不该再补一遍，更不该把管理员删掉的项加回来
        assert await service.ensure_team_permissions() == 0
        await service.layers.set_grants(SCOPE_TEAM, TEAM_SCOPE_ID, [])
        assert await service.ensure_team_permissions() == 0
        assert await service.layers.grants(SCOPE_TEAM, TEAM_SCOPE_ID) == []

    asyncio.run(_with_service(check))


def test_effective_permissions_are_the_union_of_all_four_layers() -> None:
    async def check(service: IdentityService) -> None:
        uid = await _viewer_id(service)
        group = await service.layers.create_group("并集测试组")
        await service.layers.set_grants(SCOPE_GROUP, group.id, ["template:use"])
        await service.layers.set_grants(SCOPE_USER, uid, ["template:review"])
        await service.layers.set_members(group.id, [uid])
        await service.session.commit()

        identity = await service.resolve_identity(uid)
        assert identity is not None
        # 角色层（viewer 的 7 条） + 团队层（同集合） + 小组 + 个人
        assert set(TEAM_BASE_PERMISSIONS) <= identity.permissions
        assert {"template:use", "template:review"} <= identity.permissions
        # 既不能因为小组授权丢掉角色权限，也不能因为个人授权丢掉小组权限
        assert "library:read" in identity.permissions
        assert not identity.is_superuser

    asyncio.run(_with_service(check))


def test_personal_grant_takes_effect_without_relogin(client: TestClient) -> None:
    headers = _login(client)
    denied = client.get(f"{API}/permissions", headers=headers)
    assert denied.status_code == 403

    async def grant(service: IdentityService) -> None:
        uid = await _viewer_id(service)
        await service.layers.set_grants(SCOPE_USER, uid, ["identity:read"])
        await service.session.commit()

    asyncio.run(_with_service(grant))
    # 同一张令牌：权限是每请求查库的，不需要重新登录
    assert client.get(f"{API}/permissions", headers=headers).status_code == 200


def test_multiple_groups_union_and_leaving_drops_only_that_group() -> None:
    async def check(service: IdentityService) -> None:
        uid = await _viewer_id(service)
        first = await service.layers.create_group("甲组")
        second = await service.layers.create_group("乙组")
        await service.layers.set_grants(SCOPE_GROUP, first.id, ["template:use"])
        await service.layers.set_grants(SCOPE_GROUP, second.id, ["template:review"])
        await service.layers.set_members(first.id, [uid])
        await service.layers.set_members(second.id, [uid])
        await service.session.commit()

        layers = await service.layers.layer_codes(uid)
        assert layers.group_ids and len(layers.group_ids) == 2
        assert set(layers.all_codes()) >= {"template:use", "template:review"}

        await service.layers.set_members(first.id, [])
        await service.session.commit()
        after = await service.layers.layer_codes(uid)
        assert "template:use" not in after.all_codes()
        assert "template:review" in after.all_codes()

    asyncio.run(_with_service(check))


def test_team_grants_apply_to_everyone() -> None:
    async def check(service: IdentityService) -> None:
        uid = await _viewer_id(service)
        await service.layers.set_grants(
            SCOPE_TEAM, TEAM_SCOPE_ID, [*TEAM_BASE_PERMISSIONS, "image:create"]
        )
        await service.session.commit()
        identity = await service.resolve_identity(uid)
        assert identity is not None
        assert "image:create" in identity.permissions

    asyncio.run(_with_service(check))


def test_personal_star_is_superuser_equivalent() -> None:
    async def check(service: IdentityService) -> None:
        uid = await _viewer_id(service)
        await service.layers.set_grants(SCOPE_USER, uid, ["*"])
        await service.session.commit()
        identity = await service.resolve_identity(uid)
        assert identity is not None
        assert identity.permissions == frozenset({"*"})
        assert identity.is_superuser

    asyncio.run(_with_service(check))


def test_layer_codes_keep_the_source_of_each_grant() -> None:
    async def check(service: IdentityService) -> None:
        uid = await _viewer_id(service)
        group = await service.layers.create_group("来源测试组")
        await service.layers.set_grants(SCOPE_GROUP, group.id, ["template:use"])
        await service.layers.set_grants(SCOPE_USER, uid, ["template:review"])
        await service.layers.set_members(group.id, [uid])
        await service.session.commit()

        layers = await service.layers.layer_codes(uid)
        assert isinstance(layers, LayerCodes)
        assert set(layers.team) == set(TEAM_BASE_PERMISSIONS)
        assert layers.groups == {group.id: ("template:use",)}
        assert layers.personal == ("template:review",)
        assert set(layers.all_codes()) == set(layers.team) | {
            "template:use",
            "template:review",
        }

    asyncio.run(_with_service(check))


def test_delete_group_clears_members_and_grants() -> None:
    async def check(service: IdentityService) -> None:
        uid = await _viewer_id(service)
        group = await service.layers.create_group("待删除组")
        await service.layers.set_grants(SCOPE_GROUP, group.id, ["template:use"])
        await service.layers.set_members(group.id, [uid])
        await service.session.commit()

        await service.layers.delete_group(group.id)
        await service.session.commit()
        assert await service.layers.get_group(group.id) is None
        assert (await service.layers.members_for([group.id])).get(group.id) == []
        assert await service.layers.grants(SCOPE_GROUP, group.id) == []
        assert "template:use" not in (await service.layers.layer_codes(uid)).all_codes()
        assert await service.layers.orphan_grants(set()) == []

        assert isinstance(service.layers, PermissionLayerRepository)

    asyncio.run(_with_service(check))
