"""三层授权的 HTTP 契约：团队基础 / 小组 / 个人。

守的是「界面上点得到的动作，后端也得挡得住」：
- 团队层不能放管理类权限或 `*`（否则全站超管）；
- 小组权限对成员立即生效且**免重登**；
- 个人授权不能授出操作者自己没有的权限；
- 有效权限接口把四层来源摊开，但判定只看 effective。

测试库 session 级共享，所以每个用例结束后把三层表恢复原状。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from test_identity_api import _admin, _make_user

from db.session import get_session_factory
from modules.identity.errors import GroupNotFound, PermissionEscalation
from modules.identity.models import Group, GroupMember, PermissionGrant
from modules.identity.permissions import (
    SCOPE_TEAM,
    TEAM_BASE_PERMISSIONS,
    TEAM_SCOPE_ID,
)
from modules.identity.repository import PermissionLayerRepository

API = "/api/v1/identity"


@pytest.fixture(autouse=True)
def _restore_layers() -> Iterator[None]:
    async def restore() -> None:
        async with get_session_factory()() as session:
            await session.execute(delete(GroupMember))
            await session.execute(delete(PermissionGrant))
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


def _suffix() -> str:
    return uuid.uuid4().hex[:6]


def _create_group(client: TestClient, admin: dict[str, str], name: str) -> dict:
    resp = client.post(f"{API}/groups", json={"name": name}, headers=admin)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def test_team_permissions_roundtrip_and_forbidden_codes(client: TestClient) -> None:
    admin = _admin(client)
    base = client.get(f"{API}/team/permissions", headers=admin).json()["data"]
    assert set(base) == set(TEAM_BASE_PERMISSIONS)

    updated = client.put(
        f"{API}/team/permissions",
        json={"permissions": [*TEAM_BASE_PERMISSIONS, "template_pack:create"]},
        headers=admin,
    )
    assert updated.status_code == 200, updated.text
    assert "template_pack:create" in updated.json()["data"]

    for forbidden in ("identity:read", "identity:*", "*"):
        denied = client.put(
            f"{API}/team/permissions",
            json={"permissions": [*TEAM_BASE_PERMISSIONS, forbidden]},
            headers=admin,
        )
        assert denied.status_code == 400, forbidden
        assert denied.json()["code"] == 40023

    assert set(client.get(f"{API}/team/permissions", headers=admin).json()["data"]) == {
        *TEAM_BASE_PERMISSIONS,
        "template_pack:create",
    }


def test_group_crud_members_and_permissions(client: TestClient) -> None:
    admin = _admin(client)
    name = "接口测试组-" + _suffix()
    group = _create_group(client, admin, name)
    gid = group["id"]

    listed = client.get(f"{API}/groups", headers=admin).json()["data"]
    assert any(item["id"] == gid for item in listed)

    renamed = client.patch(
        f"{API}/groups/{gid}", json={"name": name + "-改", "description": "说明"}, headers=admin
    )
    assert renamed.json()["data"]["name"] == name + "-改"

    viewer = client.get(f"{API}/users", params={"username": "viewer"}, headers=admin).json()[
        "data"
    ]["items"][0]
    members = client.put(
        f"{API}/groups/{gid}/members", json={"userIds": [viewer["id"]]}, headers=admin
    )
    assert members.json()["data"]["memberIds"] == [viewer["id"]]
    got = client.get(f"{API}/groups/{gid}/members", headers=admin).json()["data"]
    assert [item["id"] for item in got] == [viewer["id"]]

    perms = client.put(
        f"{API}/groups/{gid}/permissions",
        json={"permissions": ["template_pack:create"]},
        headers=admin,
    )
    assert perms.json()["data"]["permissions"] == ["template_pack:create"]

    # 同一张已登录令牌：小组授权立即生效，不需要重新登录
    login = client.post(
        f"{API}/login", json={"username": "viewer", "password": "viewer@12345"}
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
    assert "template_pack:create" in client.get(f"{API}/me", headers=headers).json()["data"][
        "permissions"
    ]

    assert client.delete(f"{API}/groups/{gid}", headers=admin).status_code == 200
    assert client.get(f"{API}/groups/{gid}", headers=admin).status_code == 404
    assert "template_pack:create" not in client.get(f"{API}/me", headers=headers).json()["data"][
        "permissions"
    ]


def test_group_name_must_be_unique_and_members_must_exist(client: TestClient) -> None:
    admin = _admin(client)
    name = "重名测试组-" + _suffix()
    group = _create_group(client, admin, name)

    again = client.post(f"{API}/groups", json={"name": name}, headers=admin)
    assert again.status_code == 409
    assert again.json()["code"] == 40927

    missing = client.put(
        f"{API}/groups/{group['id']}/members",
        json={"userIds": ["u-does-not-exist"]},
        headers=admin,
    )
    assert missing.status_code == 404

    assert isinstance(PermissionLayerRepository, type)


def test_personal_permissions_and_effective_sources(client: TestClient) -> None:
    admin = _admin(client)
    viewer = client.get(f"{API}/users", params={"username": "viewer"}, headers=admin).json()[
        "data"
    ]["items"][0]
    uid = viewer["id"]

    group = _create_group(client, admin, "来源组-" + _suffix())
    client.put(
        f"{API}/groups/{group['id']}/members", json={"userIds": [uid]}, headers=admin
    )
    client.put(
        f"{API}/groups/{group['id']}/permissions",
        json={"permissions": ["template_pack:create"]},
        headers=admin,
    )
    client.put(
        f"{API}/users/{uid}/permissions",
        json={"permissions": ["template_pack:publish"]},
        headers=admin,
    )

    assert client.get(f"{API}/users/{uid}/permissions", headers=admin).json()[
        "data"
    ] == ["template_pack:publish"]
    detail = client.get(
        f"{API}/users/{uid}/effective-permissions", headers=admin
    ).json()["data"]
    assert set(detail["team"]) == set(TEAM_BASE_PERMISSIONS)
    assert detail["groups"] == [
        {"id": group["id"], "name": group["name"], "permissions": ["template_pack:create"]}
    ]
    assert detail["personal"] == ["template_pack:publish"]
    assert detail["roles"]
    assert "template_pack:create" in detail["effective"]
    assert "template_pack:publish" in detail["effective"]
    assert "library:read" in detail["effective"]


def test_cannot_grant_a_permission_you_do_not_have(client: TestClient) -> None:
    admin = _admin(client)
    tag = _suffix()
    target, _, _ = _make_user(client, admin, f"layer_target_{tag}", [])
    _, ops, _ = _make_user(
        client,
        admin,
        f"layer_ops_{tag}",
        ["identity:read", "identity:user:permission:manage"],
    )

    denied = client.put(
        f"{API}/users/{target['id']}/permissions",
        json={"permissions": ["task:create"]},
        headers=ops,
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == 40323


def test_new_endpoints_are_not_covered_by_identity_read(client: TestClient) -> None:
    admin = _admin(client)
    tag = _suffix()
    _, reader, _ = _make_user(client, admin, f"layer_reader_{tag}", ["identity:read"])

    assert client.get(f"{API}/groups", headers=reader).status_code == 403
    assert (
        client.post(f"{API}/groups", json={"name": "x"}, headers=reader).status_code
        == 403
    )
    assert (
        client.put(
            f"{API}/team/permissions", json={"permissions": []}, headers=reader
        ).status_code
        == 403
    )


def test_user_list_filters_and_contact_fields(client: TestClient) -> None:
    admin = _admin(client)
    tag = _suffix()
    phone = "138" + uuid.uuid4().hex[:8]
    created = client.post(
        f"{API}/users",
        json={
            "username": f"contact_{tag}",
            "password": "pass-12345",
            "displayName": "通讯录测试",
            "phone": phone,
            "position": "拍剪",
        },
        headers=admin,
    )
    assert created.status_code == 201, created.text
    user = created.json()["data"]
    assert user["phone"] == phone and user["position"] == "拍剪"

    group = _create_group(client, admin, "筛选组-" + _suffix())
    client.put(
        f"{API}/groups/{group['id']}/members", json={"userIds": [user["id"]]}, headers=admin
    )

    by_phone = client.get(f"{API}/users", params={"phone": phone}, headers=admin).json()[
        "data"
    ]
    assert [item["id"] for item in by_phone["items"]] == [user["id"]]
    by_group = client.get(
        f"{API}/users", params={"groupId": group["id"]}, headers=admin
    ).json()["data"]
    assert [item["id"] for item in by_group["items"]] == [user["id"]]
    by_name = client.get(
        f"{API}/users", params={"name": "通讯录"}, headers=admin
    ).json()["data"]
    assert [item["id"] for item in by_name["items"]] == [user["id"]]


# --------------------------------------------------------------------------- #
# 建号时直接选分组（UserCreateIn.groupIds）
# --------------------------------------------------------------------------- #
def test_create_user_can_join_groups_and_inherits_group_permissions(
    client: TestClient,
) -> None:
    admin = _admin(client)
    group = _create_group(client, admin, "建号入组-" + _suffix())
    granted = client.put(
        f"{API}/groups/{group['id']}/permissions",
        json={"permissions": ["template_pack:create"]},
        headers=admin,
    )
    assert granted.status_code == 200, granted.text

    created = client.post(
        f"{API}/users",
        json={
            "username": f"joiner_{_suffix()}",
            "password": "pass-12345",
            # 前端下拉去重后不会传重复 id，但后端也不能因此插入两条成员
            "groupIds": [group["id"], group["id"]],
        },
        headers=admin,
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["data"]["id"]

    members = client.get(
        f"{API}/groups/{group['id']}/members", headers=admin
    ).json()["data"]
    assert [item["id"] for item in members].count(user_id) == 1

    effective = client.get(
        f"{API}/users/{user_id}/effective-permissions", headers=admin
    ).json()["data"]
    assert "template_pack:create" in effective["effective"]
    assert any(layer["id"] == group["id"] for layer in effective["groups"])

    # 不传 groupIds 的老调用仍是「不进任何组」
    plain = client.post(
        f"{API}/users",
        json={"username": f"plain_{_suffix()}", "password": "pass-12345"},
        headers=admin,
    )
    assert plain.status_code == 201, plain.text
    assert (
        client.get(
            f"{API}/users/{plain.json()['data']['id']}/effective-permissions",
            headers=admin,
        )
        .json()["data"]["groups"]
        == []
    )


def test_create_user_rejects_unknown_group(client: TestClient) -> None:
    admin = _admin(client)
    created = client.post(
        f"{API}/users",
        json={
            "username": f"unknown_group_{_suffix()}",
            "password": "pass-12345",
            "groupIds": ["g_does_not_exist"],
        },
        headers=admin,
    )
    assert created.status_code == 404
    assert created.json()["code"] == GroupNotFound.code
    assert created.json()["details"]["unknown"] == ["g_does_not_exist"]


def test_create_user_cannot_join_a_group_above_the_actor(client: TestClient) -> None:
    """建号入组和建号挂角色是同一条提权红线：不能把新人塞进比自己强的组。"""
    admin = _admin(client)
    group = _create_group(client, admin, "高权限组-" + _suffix())
    granted = client.put(
        f"{API}/groups/{group['id']}/permissions",
        json={"permissions": ["template_pack:create"]},
        headers=admin,
    )
    assert granted.status_code == 200, granted.text

    _, limited, _ = _make_user(
        client, admin, f"creator_{_suffix()}", ["identity:create"]
    )
    created = client.post(
        f"{API}/users",
        json={
            "username": f"limited_joiner_{_suffix()}",
            "password": "pass-12345",
            "groupIds": [group["id"]],
        },
        headers=limited,
    )
    assert created.status_code == 403
    assert created.json()["code"] == PermissionEscalation.code


def test_group_member_manager_cannot_add_itself_to_a_stronger_group(
    client: TestClient,
) -> None:
    """能排班 ≠ 能授权：`member:manage` 不能把任何人塞进比自己强的组。

    入组会立刻继承该组全部授权码（`resolve_identity` 把组授权并进有效权限），
    所以「分配成员」在这条路径上等价于「授权」。缺了这道守卫，
    持 `identity:group:member:manage` 的人可以把自己加进高权组、把同伙拉进去、
    或把别人踢出去（撤权），完全绕过 `identity:group:permission:manage`。
    """
    admin = _admin(client)
    group = _create_group(client, admin, "运维组-" + _suffix())
    granted = client.put(
        f"{API}/groups/{group['id']}/permissions",
        json={"permissions": ["template_pack:create"]},
        headers=admin,
    )
    assert granted.status_code == 200, granted.text

    member, limited, _ = _make_user(
        client, admin, f"roster_{_suffix()}", ["identity:group:member:manage"]
    )
    updated = client.put(
        f"{API}/groups/{group['id']}/members",
        json={"userIds": [member["id"]]},
        headers=limited,
    )
    assert updated.status_code == 403, updated.text
    assert updated.json()["code"] == PermissionEscalation.code
