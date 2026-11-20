"""「分组 → 用户」树：左栏导航的数据源。

为什么单独一个接口：用户列表是**分页**的，前端拿它拼树时人多就会静默少人，
而树是导航 —— 少一行等于少一个能配权限的人，看的人没有任何提示。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.test_pages import API, IDENTITY, _admin, _login, _suffix


def _group(client: TestClient, admin: dict[str, str]) -> dict:
    name = f"树_{_suffix()}"
    resp = client.post(f"{IDENTITY}/groups", json={"name": name}, headers=admin)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _user(client: TestClient, admin, role_id: str | None = None) -> dict:
    name = f"tree_{uuid.uuid4().hex[:6]}"
    resp = client.post(
        f"{IDENTITY}/users",
        json={
            "username": name,
            "password": "pass-12345",
            "displayName": name,
            "roleIds": [role_id] if role_id else [],
        },
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _tree(client: TestClient, headers: dict[str, str]) -> dict:
    resp = client.get(f"{IDENTITY}/permission-tree", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_tree_lists_group_members_and_ungrouped(client: TestClient) -> None:
    admin = _admin(client)
    group = _group(client, admin)
    member = _user(client, admin)
    loner = _user(client, admin)

    client.put(
        f"{IDENTITY}/groups/{group['id']}/members",
        json={"userIds": [member["id"]]},
        headers=admin,
    )

    tree = _tree(client, admin)
    node = next(item for item in tree["groups"] if item["id"] == group["id"])
    assert [m["id"] for m in node["members"]] == [member["id"]]
    assert node["members"][0]["username"] == member["username"]

    ungrouped = {item["id"] for item in tree["ungrouped"]}
    assert loner["id"] in ungrouped
    # 已经在组里的人不再出现在「未分组」里
    assert member["id"] not in ungrouped


def test_tree_is_complete_not_paged(client: TestClient) -> None:
    """树必须一次给全：分页少一页时，看的人会以为那就是全部。

    这里用「全部启用账号都出现在树里」来钉 —— 组内 + 未分组 == 所有账号。
    """
    admin = _admin(client)
    # 用户列表最多 100 一页（`MAX_PAGE_SIZE`），所以要翻完才能拿到全集 ——
    # 这正好说明「用列表拼树」在人多时必然少人
    everyone: list[str] = []
    page = 1
    while True:
        chunk = client.get(
            f"{IDENTITY}/users", params={"page": page, "pageSize": 100}, headers=admin
        ).json()["data"]
        everyone += [item["id"] for item in chunk["items"]]
        if len(everyone) >= chunk["total"] or not chunk["items"]:
            break
        page += 1

    tree = _tree(client, admin)
    in_tree = {m["id"] for g in tree["groups"] for m in g["members"]}
    in_tree |= {m["id"] for m in tree["ungrouped"]}

    assert in_tree == set(everyone)


def test_tree_shows_inactive_accounts(client: TestClient) -> None:
    """停用账号也要在树上：他可能还挂着角色/小组的授权。

    「已经不是员工、权限还在」正是需要被看见的情况；隐藏掉等于让它消失。
    """
    admin = _admin(client)
    gone = _user(client, admin)
    client.delete(f"{IDENTITY}/users/{gone['id']}", headers=admin)

    tree = _tree(client, admin)
    everyone = {m["id"]: m for g in tree["groups"] for m in g["members"]}
    everyone |= {m["id"]: m for m in tree["ungrouped"]}

    assert gone["id"] in everyone
    assert everyone[gone["id"]]["isActive"] is False
