"""共享与撤销审批（设计 §3.2 / §3.5）。

重点不是「正常路径能跑通」，而是三件容易被实现成「看起来对」的事：

1. **共享没审批通过之前，组里其他人看不到**（否则审批只是个按钮）；
2. **组长能审批本组申请，即使他没有 `library:share:approve`**（只要求 read，
   把判定放在服务层）；
3. **撤销申请 pending 期间，删除必须 409**（否则「取消共享才能删」被绕过）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    fetch_asset,
    fetch_group_rows,
    make_group,
    make_user,
    upload_image,
)

SHARE_PERMISSIONS = LIB_RW + ["library:tag:manage"]
READ_ONLY = ["library:read"]


def _share(
    client: TestClient,
    api: str,
    headers: dict[str, str],
    asset_id: str,
    *,
    group_id: str = "",
    target: str = "group",
    action: str = "share",
    reason: str = "",
):
    return client.post(
        f"{api}/library/share-requests",
        json={
            "assetId": asset_id,
            "action": action,
            "target": target,
            "groupId": group_id,
            "reason": reason,
        },
        headers=headers,
    )


def test_share_requires_approval_then_unlocks_group_visibility(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    viewer, viewer_h = make_user(client, admin, READ_ONLY)
    group = make_group(client, admin, [owner["id"], viewer["id"]])
    asset = upload_image(client, api, owner_h, color="navy")

    created = _share(client, api, owner_h, asset["id"], group_id=group["id"])
    assert created.status_code == 201, created.text
    request = created.json()["data"]
    assert request["status"] == "pending", "没有 share:direct 的人必须先走审批"
    assert request["groupName"] == group["name"]

    # 审批通过之前：申请人自己在小组库里也不该看到（visibility 还是 personal）
    before = client.get(
        f"{api}/library/assets", params={"library": "group"}, headers=viewer_h
    ).json()["data"]
    assert asset["id"] not in [item["id"] for item in before["items"]]

    approved = client.post(
        f"{api}/library/share-requests/{request['id']}/approve",
        json={"note": "同意"},
        headers=admin,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["status"] == "approved"

    after = client.get(
        f"{api}/library/assets", params={"library": "group"}, headers=viewer_h
    ).json()["data"]
    assert asset["id"] in [item["id"] for item in after["items"]]
    # 可见即可读：详情与原件都要能拿到
    assert (
        client.get(f"{api}/library/assets/{asset['id']}", headers=viewer_h).status_code
        == 200
    )
    detail = client.get(f"{api}/library/assets/{asset['id']}", headers=viewer_h).json()["data"]
    assert detail["visibility"] == "group"
    assert [item["id"] for item in detail["groups"]] == [group["id"]]

    # 个人库不再重复出现（共享出去之后它在小组库）
    personal = client.get(
        f"{api}/library/assets", params={"library": "personal"}, headers=owner_h
    ).json()["data"]
    assert asset["id"] not in [item["id"] for item in personal["items"]]


def test_group_leader_can_approve_without_global_permission(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    leader, leader_h = make_user(client, admin, SHARE_PERMISSIONS)
    group = make_group(client, admin, [owner["id"], leader["id"]])
    asset = upload_image(client, api, owner_h, color="teal")

    set_leaders = client.put(
        f"{api}/library/groups/{group['id']}/leaders",
        json={"userIds": [leader["id"]]},
        headers=admin,
    )
    assert set_leaders.status_code == 200, set_leaders.text

    mine = client.get(f"{api}/library/groups", headers=leader_h).json()["data"]
    assert any(item["id"] == group["id"] and item["isLeader"] for item in mine)

    request = _share(client, api, owner_h, asset["id"], group_id=group["id"]).json()["data"]
    pending = client.get(
        f"{api}/library/share-requests",
        params={"scope": "pending"},
        headers=leader_h,
    ).json()["data"]
    assert request["id"] in [item["id"] for item in pending]

    # 组长没有 library:share:approve，但本组申请由他审
    approved = client.post(
        f"{api}/library/share-requests/{request['id']}/approve",
        json={},
        headers=leader_h,
    )
    assert approved.status_code == 200, approved.text


def test_approver_scope_does_not_leak_other_groups(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    outsider, outsider_h = make_user(client, admin, SHARE_PERMISSIONS)
    group_a = make_group(client, admin, [owner["id"]])
    make_group(client, admin, [outsider["id"]])
    asset = upload_image(client, api, owner_h, color="olive")

    request = _share(client, api, owner_h, asset["id"], group_id=group_a["id"]).json()["data"]

    # outsider 是另一个组的（非组长），"待我审核" 里不该有别人的申请
    pending = client.get(
        f"{api}/library/share-requests",
        params={"scope": "pending"},
        headers=outsider_h,
    ).json()["data"]
    assert request["id"] not in [item["id"] for item in pending]

    # 也不能越权审批
    denied = client.post(
        f"{api}/library/share-requests/{request['id']}/approve",
        json={},
        headers=outsider_h,
    )
    assert denied.status_code == 403, denied.text


def test_revoke_needs_reason_and_blocks_delete_while_pending(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, color="brown")

    share = _share(client, api, owner_h, asset["id"], group_id=group["id"]).json()["data"]
    client.post(
        f"{api}/library/share-requests/{share['id']}/approve", json={}, headers=admin
    )

    # 已共享：直接删是 409，且告诉前端这个资产走不了窄口（owner 不是组长）
    blocked = client.delete(f"{api}/library/assets/{asset['id']}", headers=owner_h)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == 40981
    assert blocked.json()["details"]["canRevokeAndDelete"] is False

    no_reason = _share(
        client, api, owner_h, asset["id"], group_id=group["id"], action="revoke"
    )
    assert no_reason.status_code == 400
    assert no_reason.json()["code"] == 40081

    revoke = _share(
        client,
        api,
        owner_h,
        asset["id"],
        group_id=group["id"],
        action="revoke",
        reason="改用另一版",
    )
    assert revoke.status_code == 201, revoke.text
    assert revoke.json()["data"]["status"] == "pending"

    # 撤销审批中：删除必须挡住，提示「撤销审批中」
    still_blocked = client.delete(f"{api}/library/assets/{asset['id']}", headers=owner_h)
    assert still_blocked.status_code == 409
    assert still_blocked.json()["code"] == 40983

    client.post(
        f"{api}/library/share-requests/{revoke.json()['data']['id']}/approve",
        json={},
        headers=admin,
    )
    row = fetch_asset(asset["id"])
    assert row is not None and row["visibility"] == "personal"
    assert fetch_group_rows(asset["id"]) == []

    deleted = client.delete(f"{api}/library/assets/{asset['id']}", headers=owner_h)
    assert deleted.status_code == 200, deleted.text
    row = fetch_asset(asset["id"])
    assert row is not None and row["state"] == "trash"
    assert row["trashed_from_state"] == "active"
    # 不变式：进回收站必须 personal
    assert row["visibility"] == "personal"


def test_revoke_by_direct_share_holder_still_needs_approval(
    client: TestClient, api: str
) -> None:
    """能直接共享 ≠ 能直接收回：撤销的风险方向相反，一律审批（设计 §3.5）。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(
        client, admin, SHARE_PERMISSIONS + ["library:share:direct"]
    )
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, color="maroon")

    direct = _share(client, api, owner_h, asset["id"], group_id=group["id"]).json()["data"]
    assert direct["status"] == "approved", "有 share:direct 时共享直接生效"
    assert fetch_asset(asset["id"])["visibility"] == "group"  # type: ignore[index]

    revoke = _share(
        client,
        api,
        owner_h,
        asset["id"],
        group_id=group["id"],
        action="revoke",
        reason="不再共享",
    ).json()["data"]
    assert revoke["status"] == "pending", "撤销不因 share:direct 免审"
    assert fetch_asset(asset["id"])["visibility"] == "group"  # type: ignore[index]


def test_cannot_share_to_a_group_i_am_not_in(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    other, _ = make_user(client, admin, READ_ONLY)
    foreign = make_group(client, admin, [other["id"]])
    asset = upload_image(client, api, owner_h, color="silver")

    response = _share(client, api, owner_h, asset["id"], group_id=foreign["id"])
    assert response.status_code == 409
    assert response.json()["code"] == 40987


def test_share_to_team_is_visible_to_everyone(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    stranger, stranger_h = make_user(client, admin, READ_ONLY)
    asset = upload_image(client, api, owner_h, color="gold")

    request = _share(client, api, owner_h, asset["id"], target="team").json()["data"]
    client.post(
        f"{api}/library/share-requests/{request['id']}/approve", json={}, headers=admin
    )

    team = client.get(
        f"{api}/library/assets", params={"library": "team"}, headers=stranger_h
    ).json()["data"]
    assert asset["id"] in [item["id"] for item in team["items"]]

    # 团队库资产连上传者本人也不能直接删
    blocked = client.delete(f"{api}/library/assets/{asset['id']}", headers=owner_h)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == 40982
