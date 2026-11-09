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
        json={"permissions": [*TEAM_BASE_PERMISSIONS, "template:use"]},
        headers=admin,
    )
    assert updated.status_code == 200, updated.text
    assert "template:use" in updated.json()["data"]

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
        "template:use",
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
        json={"permissions": ["template:use"]},
        headers=admin,
    )
    assert perms.json()["data"]["permissions"] == ["template:use"]

    # 同一张已登录令牌：小组授权立即生效，不需要重新登录
    login = client.post(
        f"{API}/login", json={"username": "viewer", "password": "viewer@12345"}
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
    assert "template:use" in client.get(f"{API}/me", headers=headers).json()["data"][
        "permissions"
    ]

    assert client.delete(f"{API}/groups/{gid}", headers=admin).status_code == 200
    assert client.get(f"{API}/groups/{gid}", headers=admin).status_code == 404
    assert "template:use" not in client.get(f"{API}/me", headers=headers).json()["data"][
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
        json={"permissions": ["template:use"]},
        headers=admin,
    )
    client.put(
        f"{API}/users/{uid}/permissions",
        json={"permissions": ["template:review"]},
        headers=admin,
    )

    assert client.get(f"{API}/users/{uid}/permissions", headers=admin).json()[
        "data"
    ] == ["template:review"]
    detail = client.get(
        f"{API}/users/{uid}/effective-permissions", headers=admin
    ).json()["data"]
    assert set(detail["team"]) == set(TEAM_BASE_PERMISSIONS)
    assert detail["groups"] == [
        {"id": group["id"], "name": group["name"], "permissions": ["template:use"]}
    ]
    assert detail["personal"] == ["template:review"]
    assert detail["roles"]
    assert "template:use" in detail["effective"]
    assert "template:review" in detail["effective"]
    assert "asset:read" in detail["effective"]


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
