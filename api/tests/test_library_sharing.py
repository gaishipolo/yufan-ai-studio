"""共享与撤销审批（设计 §3.2 / §3.5）。

重点不是「正常路径能跑通」，而是三件容易被实现成「看起来对」的事：

1. **共享没审批通过之前，组里其他人看不到**（否则审批只是个按钮）；
2. **审批资格按申请目标范围判定**（路由只要求 read，判定放在服务层）：
   小组审批码 + 本组成员关系；登记为组长本身不再发审批票；
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


def test_group_approver_can_approve_without_being_leader(
    client: TestClient, api: str
) -> None:
    """拆分后：审批资格 = 小组审批码 + 我是该组成员，与组长登记无关。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    approver, approver_h = make_user(
        client, admin, READ_ONLY + ["library:share:approve:group"]
    )
    group = make_group(client, admin, [owner["id"], approver["id"]])
    asset = upload_image(client, api, owner_h, color="teal")

    # 故意不登记负责人：这张表现在只服务删除窄口
    set_leaders = client.put(
        f"{api}/library/groups/{group['id']}/leaders",
        json={"userIds": []},
        headers=admin,
    )
    assert set_leaders.status_code == 200, set_leaders.text

    mine = client.get(f"{api}/library/groups", headers=approver_h).json()["data"]
    assert any(item["id"] == group["id"] and not item["isLeader"] for item in mine)

    request = _share(client, api, owner_h, asset["id"], group_id=group["id"]).json()["data"]
    pending = client.get(
        f"{api}/library/share-requests",
        params={"scope": "pending"},
        headers=approver_h,
    ).json()["data"]
    assert request["id"] in [item["id"] for item in pending]

    # 没有全站审批码、也不是组长，但本组申请由他审
    approved = client.post(
        f"{api}/library/share-requests/{request['id']}/approve",
        json={},
        headers=approver_h,
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


def test_group_member_can_ask_to_widen_a_shared_asset_to_team(
    client: TestClient, api: str
) -> None:
    """组员能把「组内可见」的别人资产提到团队，但必须经审批才生效。

    共享申请是**请求变更可见性**，审批人存在就是为了把关「扩大范围」。要求
    owner 才能提，等于审批流只服务自己的东西——组员看到一份该全员复用的素材，
    除了找上传者外没有别的路，而这条路上传者可能已经离职。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    member, member_h = make_user(
        client, admin, LIB_RW + ["library:share"], group_ids=[]
    )
    outsider, outsider_h = make_user(client, admin, READ_ONLY)
    group = make_group(client, admin, [owner["id"], member["id"]])
    asset = upload_image(client, api, owner_h, color="coral")

    first = _share(client, api, owner_h, asset["id"], group_id=group["id"]).json()["data"]
    client.post(
        f"{api}/library/share-requests/{first['id']}/approve", json={}, headers=admin
    )

    response = _share(client, api, member_h, asset["id"], target="team")
    assert response.status_code == 201, response.text
    created = response.json()["data"]
    assert created["status"] == "pending"
    assert created["applicantId"] == member["id"]

    # 审批通过前，组外的人还看不到
    hidden = client.get(f"{api}/library/assets/{asset['id']}", headers=outsider_h)
    assert hidden.status_code == 404

    client.post(
        f"{api}/library/share-requests/{created['id']}/approve", json={}, headers=admin
    )
    assert fetch_asset(asset["id"])["visibility"] == "team"
    visible = client.get(f"{api}/library/assets/{asset['id']}", headers=outsider_h)
    assert visible.status_code == 200


def test_member_request_never_applies_directly_even_with_direct_permission(
    client: TestClient, api: str
) -> None:
    """替别人扩大可见范围，即使持 `library:share:direct` 也必须留审批。

    `direct` 的语义是「自己的东西自己说了算」；对别人的资产，审批记录是这条
    不变式的全部载体，跳过它就等于管理员能悄悄把任何人的私货推上团队库。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    member, member_h = make_user(
        client,
        admin,
        LIB_RW + ["library:share", "library:share:direct"],
        group_ids=[],
    )
    group = make_group(client, admin, [owner["id"], member["id"]])
    asset = upload_image(client, api, owner_h, color="plum")

    first = _share(client, api, owner_h, asset["id"], group_id=group["id"]).json()["data"]
    client.post(
        f"{api}/library/share-requests/{first['id']}/approve", json={}, headers=admin
    )

    created = _share(client, api, member_h, asset["id"], target="team").json()["data"]
    assert created["status"] == "pending"
    assert fetch_asset(asset["id"])["visibility"] == "group"


def test_member_cannot_revoke_someone_elses_share(client: TestClient, api: str) -> None:
    """撤销相反：风险落在别人身上，只有所有者（或审批人）能提。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    member, member_h = make_user(client, admin, LIB_RW + ["library:share"])
    group = make_group(client, admin, [owner["id"], member["id"]])
    asset = upload_image(client, api, owner_h, color="khaki")

    created = _share(client, api, owner_h, asset["id"], group_id=group["id"]).json()[
        "data"
    ]
    client.post(
        f"{api}/library/share-requests/{created['id']}/approve", json={}, headers=admin
    )

    response = _share(
        client, api, member_h, asset["id"], group_id=group["id"], action="revoke",
        reason="我觉得该收回去",
    )
    assert response.status_code == 403
    assert response.json()["code"] == 40382
    assert fetch_asset(asset["id"])["visibility"] == "group"


def test_outsider_cannot_ask_to_share_an_invisible_asset(
    client: TestClient, api: str
) -> None:
    """看不见的资产仍按 404 处理，别把申请入口变成「猜 id 探库」。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    stranger, stranger_h = make_user(client, admin, LIB_RW + ["library:share"])
    asset = upload_image(client, api, owner_h, color="gray")

    response = _share(client, api, stranger_h, asset["id"], target="team")
    assert response.status_code == 404


def test_pack_member_cannot_be_shared_on_its_own(client: TestClient, api: str) -> None:
    """只靠「封装成员」读到的素材不能单独调共享范围。

    成员的可见范围跟着封装走；允许单条放宽，封装内部就出现「整体给我看，
    但其中一张全团队可见」的裂缝，撤销时也无从判断该收哪一层。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    member, member_h = make_user(client, admin, LIB_RW + ["library:share"])
    group = make_group(client, admin, [owner["id"], member["id"]])
    asset = upload_image(client, api, owner_h, color="green")
    created = client.post(
        f"{api}/library/packs",
        json={"name": "共享封装", "assetIds": [asset["id"]]},
        headers=owner_h,
    )
    assert created.status_code == 201, created.text
    pack = created.json()["data"]

    shared = _share(client, api, owner_h, pack["id"], group_id=group["id"]).json()["data"]
    client.post(
        f"{api}/library/share-requests/{shared['id']}/approve", json={}, headers=admin
    )

    response = _share(client, api, member_h, asset["id"], target="team")
    assert response.status_code == 403
    assert response.json()["code"] == 40383

    # 所有者自己不受这条限制：那是他的东西
    assert (
        _share(client, api, owner_h, asset["id"], target="team").status_code == 201
    )


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
