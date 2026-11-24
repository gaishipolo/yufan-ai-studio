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


def test_cannot_share_to_a_group_i_am_not_in(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    other, _ = make_user(client, admin, READ_ONLY)
    foreign = make_group(client, admin, [other["id"]])
    asset = upload_image(client, api, owner_h, color="silver")

    response = _share(client, api, owner_h, asset["id"], group_id=foreign["id"])
    assert response.status_code == 409
    assert response.json()["code"] == 40987


def test_approving_a_share_after_the_asset_went_to_trash_is_refused(
    client: TestClient, api: str
) -> None:
    """审批通过前必须复核资产状态：回收站里的条目不能被改成共享可见。

    申请单是快照：申请时成立，不代表审批时还成立。原来的 `decide()` 只取行、
    不判 state，于是「移进回收站 → 审批通过」会在回收站里留下一条
    `state=trash ∧ visibility=team` 的行，破坏不变式 A/B（回收站里的东西继续
    出现在小组库里，「删除」就失去了语义）。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, color="navy")

    request = _share(client, api, owner_h, asset["id"], group_id=group["id"]).json()["data"]
    assert request["status"] == "pending"

    # 审批之前，资产被移进回收站
    assert client.delete(f"{api}/library/assets/{asset['id']}", headers=owner_h).status_code == 200

    approved = client.post(
        f"{api}/library/share-requests/{request['id']}/approve", json={}, headers=admin
    )
    assert approved.status_code == 409, approved.text

    row = fetch_asset(asset["id"])
    assert row is not None
    assert row["state"] == "trash"
    assert row["visibility"] == "personal", "回收站里的条目不能被改成共享可见"
    assert fetch_group_rows(asset["id"]) == [], "回收站里不能留下小组共享行"


def test_rejecting_an_outdated_request_is_still_allowed(
    client: TestClient, api: str
) -> None:
    """驳回一条已经过时的申请单是正当操作，不能被 409 拦住。

    目标有效性复核只在「通过」时做：否则待办列表里会留下一条谁也处理不了的
    单子（通过不行、驳回也不行）。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, color="plum")

    request = _share(
        client, api, owner_h, asset["id"], group_id=group["id"]
    ).json()["data"]
    assert client.delete(f"{api}/library/assets/{asset['id']}", headers=owner_h).status_code == 200

    rejected = client.post(
        f"{api}/library/share-requests/{request['id']}/reject", json={}, headers=admin
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["status"] == "rejected"


def test_approving_an_old_group_request_does_not_downgrade_a_team_share(
    client: TestClient, api: str
) -> None:
    """旧的组级申请不能把团队共享降级成小组共享。

    团队共享会清空小组行（团队 ⊇ 小组）。这时那条还挂着的组级申请已经过时，
    原来的 `_apply_share(GROUP)` 无条件写 `visibility="group"` 并补回组行——
    素材从团队库整体消失，而且这次降级**没有任何撤销申请单**。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, color="teal")

    group_request = _share(
        client, api, owner_h, asset["id"], group_id=group["id"]
    ).json()["data"]
    team_request = _share(client, api, owner_h, asset["id"], target="team").json()["data"]

    # 团队共享先通过：它会清空小组行
    approved_team = client.post(
        f"{api}/library/share-requests/{team_request['id']}/approve", json={}, headers=admin
    )
    assert approved_team.status_code == 200, approved_team.text
    assert fetch_asset(asset["id"])["visibility"] == "team"  # type: ignore[index]

    # 这时那条组级申请已经过时：通过它会把团队共享降级
    approved_group = client.post(
        f"{api}/library/share-requests/{group_request['id']}/approve", json={}, headers=admin
    )
    assert approved_group.status_code == 409, approved_group.text

    row = fetch_asset(asset["id"])
    assert row is not None and row["visibility"] == "team", "团队共享不能被旧申请降级"
    assert fetch_group_rows(asset["id"]) == []


def test_approving_an_old_revoke_does_not_wipe_a_team_share(
    client: TestClient, api: str
) -> None:
    """旧的撤销申请不能把团队共享无声抹掉。

    组行已经没了（团队共享清掉的），撤销却还能通过：`_apply_revoke(GROUP)`
    删不到组行 → `remaining=None` → `visibility="personal"`——团队共享就此
    消失，而且没有对应的撤销申请单。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, color="olive")

    share = _share(client, api, owner_h, asset["id"], group_id=group["id"]).json()["data"]
    client.post(f"{api}/library/share-requests/{share['id']}/approve", json={}, headers=admin)
    assert fetch_asset(asset["id"])["visibility"] == "group"  # type: ignore[index]

    revoke = _share(
        client,
        api,
        owner_h,
        asset["id"],
        group_id=group["id"],
        action="revoke",
        reason="改用另一版",
    ).json()["data"]
    assert revoke["status"] == "pending"

    # 又共享给了团队：组行被清空
    team = _share(client, api, owner_h, asset["id"], target="team").json()["data"]
    client.post(f"{api}/library/share-requests/{team['id']}/approve", json={}, headers=admin)
    assert fetch_asset(asset["id"])["visibility"] == "team"  # type: ignore[index]

    # 这条撤销已经过时：通过它会把团队共享抹成 personal
    approved = client.post(
        f"{api}/library/share-requests/{revoke['id']}/approve", json={}, headers=admin
    )
    assert approved.status_code == 409, approved.text
    row = fetch_asset(asset["id"])
    assert row is not None and row["visibility"] == "team", "团队共享不能被旧撤销抹掉"


def test_share_request_checks_the_expected_version(
    client: TestClient, api: str
) -> None:
    """发起申请时也要看版本：按旧页面发出的申请单必须 409。

    这条链上原来没人消费 `expected_version`：批量撤销共享把它一路传下来，
    只存进申请单就没人看了。基于旧页面发出的撤销申请会作用在用户没见过的
    资产状态上——而撤销恰恰是风险方向最重的一侧。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, color="navy")

    stale = client.post(
        f"{api}/library/share-requests",
        json={
            "assetId": asset["id"],
            "action": "share",
            "target": "group",
            "groupId": group["id"],
            "expectedVersion": 999,
        },
        headers=owner_h,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["details"]["stateVersion"] == fetch_asset(asset["id"])["state_version"]  # type: ignore[index]

    # 版本对得上就正常受理
    current = fetch_asset(asset["id"])["state_version"]  # type: ignore[index]
    ok = client.post(
        f"{api}/library/share-requests",
        json={
            "assetId": asset["id"],
            "action": "share",
            "target": "group",
            "groupId": group["id"],
            "expectedVersion": current,
        },
        headers=owner_h,
    )
    assert ok.status_code == 201, ok.text


def test_approval_version_check_uses_the_locked_read() -> None:
    """审批的乐观锁校验与写入必须落在**同一次带锁读取**上。

    原实现校验用普通读、落地用抢占后的带锁读，两次之间别人推进的版本会被
    整段跳过 —— 审批人按界面上的版本点了通过，实际作用在更新的资产上。
    """
    import ast
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "src/modules/library/sharing.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    decide = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "decide"
    )
    loads = {
        node.func.attr
        for node in ast.walk(decide)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "load_asset_for_update" in loads
    assert "load_asset" not in loads, "校验与写入用了两次读取，版本推进会被漏掉"


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


# --------------------------------------------------------------------------- #
# 审批端的乐观锁（P0-1）
#
# 申请单是快照：申请时成立，不代表审批时还成立。修复前前端固定传
# `expectedVersion: 0`，后端 `decide()` 又把 0 当成「不校验」，于是
# 「基于旧界面的审批」照样生效——共享审批看着有并发保护，实际没有
# （见 REVIEW_2026-11-23 严重#1）。
# --------------------------------------------------------------------------- #
def test_share_request_records_the_asset_version_snapshot(
    client: TestClient, api: str
) -> None:
    """申请单要记住申请时的资产版本。

    `row.expected_version` 以前是个死列：只写不读。它现在的两个职责是——
    审批端的兜底比对基准、前端「资产已变更」提示的依据。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, color="navy")

    request = _share(client, api, owner_h, asset["id"], group_id=group["id"]).json()["data"]
    assert request["status"] == "pending"
    assert request["requestStateVersion"] == asset["stateVersion"]
    assert request["stateVersion"] == asset["stateVersion"]


def test_approving_with_a_stale_version_is_rejected(
    client: TestClient, api: str
) -> None:
    """审批人看到的版本必须还是当前版本，否则 409。

    旧实现的版本判断是 `if payload.expected_version and ...`：前端固定传 0，
    判断长期空转。改成「审批人回传看到的版本」之后，基于旧界面的审批
    会被拦住，刷新后按当前版本再点「通过」才能生效。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, color="navy")

    request = _share(client, api, owner_h, asset["id"], group_id=group["id"]).json()["data"]
    seen_version = request["stateVersion"]

    # 审批人打开列表之后，资产被改了名（state_version 前进）
    renamed = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"name": "改过的名字"},
        headers=owner_h,
    )
    assert renamed.status_code == 200, renamed.text
    current_version = renamed.json()["data"]["stateVersion"]
    assert current_version != seen_version, "改名必须推进 state_version，否则这条用例没意义"

    # 按自己看到的那一版点「通过」→ 409
    stale = client.post(
        f"{api}/library/share-requests/{request['id']}/approve",
        json={"expectedVersion": seen_version},
        headers=admin,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["details"]["stateVersion"] == current_version

    # 刷新后按当前版本再点「通过」→ 200
    fresh = client.post(
        f"{api}/library/share-requests/{request['id']}/approve",
        json={"expectedVersion": current_version},
        headers=admin,
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["data"]["status"] == "approved"


def test_rejecting_with_a_stale_version_is_still_allowed(
    client: TestClient, api: str
) -> None:
    """驳回不受版本校验限制。

    版本判断只在「通过」时做：驳回一条过时的申请单是正当操作（正是把它关掉的
    时候）。修复前版本判断写在 approve/reject 分支之前，驳回时也会被 409
    拦住——于是「按旧界面驳回」这条最安全的操作反而做不了。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, color="navy")

    request = _share(client, api, owner_h, asset["id"], group_id=group["id"]).json()["data"]
    seen_version = request["stateVersion"]

    # 资产改名，版本前进
    renamed = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"name": "改过的名字"},
        headers=owner_h,
    )
    assert renamed.status_code == 200, renamed.text

    # 按旧版本驳回：必须放行
    rejected = client.post(
        f"{api}/library/share-requests/{request['id']}/reject",
        json={"expectedVersion": seen_version},
        headers=admin,
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["status"] == "rejected"
