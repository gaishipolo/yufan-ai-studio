"""审批权限拆分（2026-11-10）：按**申请的目标范围**分三码。

旧码 `library:share:approve` 保留为全站审批；新增的两个按 target 拆：
`...:approve:team` 管团队级申请，`...:approve:group` 管「我所在小组」的申请。
共享与撤销共用同一份口径，三处判定（审批 / 待我审核 / 撤销自审）必须一致。

这里刻意把「越权」写成正向断言：只读一个权限点有没有放行是不够的，
要证明**另一种目标范围确实被挡住**，否则拆分只是多了两个名字。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_group, make_user, upload_image

READ = ["library:read"]
TEAM_APPROVE = ["library:share:approve:team"]
GROUP_APPROVE = ["library:share:approve:group"]
ALL_APPROVE = ["library:share:approve"]
DIRECT = ["library:share:direct"]


def _request(
    client: TestClient,
    api: str,
    headers: dict[str, str],
    asset_id: str,
    *,
    target: str = "group",
    group_id: str = "",
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


def _decide(
    client: TestClient,
    api: str,
    headers: dict[str, str],
    request_id: str,
    *,
    approve: bool = True,
):
    action = "approve" if approve else "reject"
    return client.post(
        f"{api}/library/share-requests/{request_id}/{action}",
        json={},
        headers=headers,
    )


def _pending_ids(
    client: TestClient, api: str, headers: dict[str, str]
) -> list[str]:
    response = client.get(
        f"{api}/library/share-requests",
        params={"scope": "pending"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["data"]]


def test_team_approver_only_sees_and_decides_team_requests(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    approver, approver_h = make_user(client, admin, READ + TEAM_APPROVE)
    group = make_group(client, admin, [owner["id"], approver["id"]])
    asset = upload_image(client, api, owner_h, color="navy")

    # 两条 pending 目标不同，`_pending` 的幂等键不会互相吞掉
    group_req = _request(
        client, api, owner_h, asset["id"], group_id=group["id"]
    ).json()["data"]
    team_req = _request(client, api, owner_h, asset["id"], target="team").json()["data"]
    assert group_req["status"] == team_req["status"] == "pending"

    visible = _pending_ids(client, api, approver_h)
    assert team_req["id"] in visible
    assert group_req["id"] not in visible, "团队审批码不该看见小组申请"

    assert _decide(client, api, approver_h, team_req["id"]).status_code == 200
    assert _decide(client, api, approver_h, group_req["id"]).status_code == 403


def test_group_approver_is_limited_to_groups_they_belong_to(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner_a, owner_a_h = make_user(client, admin, LIB_RW)
    owner_b, owner_b_h = make_user(client, admin, LIB_RW)
    approver, approver_h = make_user(client, admin, READ + GROUP_APPROVE)
    group_a = make_group(client, admin, [owner_a["id"], approver["id"]])
    group_b = make_group(client, admin, [owner_b["id"]])
    asset_a = upload_image(client, api, owner_a_h, color="teal")
    asset_b = upload_image(client, api, owner_b_h, color="olive")

    in_group = _request(
        client, api, owner_a_h, asset_a["id"], group_id=group_a["id"]
    ).json()["data"]
    out_group = _request(
        client, api, owner_b_h, asset_b["id"], group_id=group_b["id"]
    ).json()["data"]

    visible = _pending_ids(client, api, approver_h)
    assert in_group["id"] in visible
    assert out_group["id"] not in visible, "不在组里就不该看见该组的申请"

    assert _decide(client, api, approver_h, in_group["id"]).status_code == 200
    assert _decide(client, api, approver_h, out_group["id"]).status_code == 403


def test_group_approver_outside_any_group_sees_nothing(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    approver, approver_h = make_user(client, admin, READ + GROUP_APPROVE)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, color="purple")
    request = _request(
        client, api, owner_h, asset["id"], group_id=group["id"]
    ).json()["data"]

    assert request["id"] not in _pending_ids(client, api, approver_h)
    assert _decide(client, api, approver_h, request["id"]).status_code == 403


def test_legacy_global_code_still_covers_both_targets(
    client: TestClient, api: str
) -> None:
    """旧码是兼容通道：勾了它等于两个细分码都勾，已有角色升级不丢能力。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    approver, approver_h = make_user(client, admin, READ + ALL_APPROVE)
    group = make_group(client, admin, [owner["id"], approver["id"]])
    asset = upload_image(client, api, owner_h, color="green")

    group_req = _request(
        client, api, owner_h, asset["id"], group_id=group["id"]
    ).json()["data"]
    team_req = _request(client, api, owner_h, asset["id"], target="team").json()["data"]

    visible = _pending_ids(client, api, approver_h)
    assert group_req["id"] in visible and team_req["id"] in visible
    assert _decide(client, api, approver_h, group_req["id"]).status_code == 200
    assert _decide(client, api, approver_h, team_req["id"]).status_code == 200


def test_leader_record_alone_no_longer_grants_approval(
    client: TestClient, api: str
) -> None:
    """组长表按产品口径退回去只服务删除窄口，不再单独发审批票。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    leader, leader_h = make_user(client, admin, READ)
    group = make_group(client, admin, [owner["id"], leader["id"]])
    set_leaders = client.put(
        f"{api}/library/groups/{group['id']}/leaders",
        json={"userIds": [leader["id"]]},
        headers=admin,
    )
    assert set_leaders.status_code == 200, set_leaders.text

    # 表还在、`isLeader` 还在（窄口与界面要用），只是不再等于审批资格
    groups = client.get(f"{api}/library/groups", headers=leader_h).json()["data"]
    assert any(item["id"] == group["id"] and item["isLeader"] for item in groups)

    asset = upload_image(client, api, owner_h, color="maroon")
    request = _request(
        client, api, owner_h, asset["id"], group_id=group["id"]
    ).json()["data"]
    assert request["id"] not in _pending_ids(client, api, leader_h)
    assert _decide(client, api, leader_h, request["id"]).status_code == 403


def test_revoke_self_review_follows_target_scope(
    client: TestClient, api: str
) -> None:
    """「自己申请、等自己批」的死锁只按**该目标**的审批资格解锁。

    只有小组审批码的人撤销自己共享到团队的资产，不能顺手跳过团队级审批。
    """
    admin = admin_headers(client)

    # A：小组审批码 + 撤销团队共享 → 不自审
    _, group_owner_h = make_user(
        client, admin, LIB_RW + DIRECT + GROUP_APPROVE
    )
    asset_a = upload_image(client, api, group_owner_h, color="gray")
    shared_a = _request(client, api, group_owner_h, asset_a["id"], target="team")
    assert shared_a.status_code == 201, shared_a.text
    assert shared_a.json()["data"]["status"] == "approved"
    revoked_a = _request(
        client,
        api,
        group_owner_h,
        asset_a["id"],
        target="team",
        action="revoke",
        reason="不再需要全员可见",
    )
    assert revoked_a.status_code == 201, revoked_a.text
    assert revoked_a.json()["data"]["status"] == "pending"

    # B：团队审批码 + 撤销团队共享 → 自审
    _, team_owner_h = make_user(client, admin, LIB_RW + DIRECT + TEAM_APPROVE)
    asset_b = upload_image(client, api, team_owner_h, color="khaki")
    assert (
        _request(client, api, team_owner_h, asset_b["id"], target="team")
        .json()["data"]["status"]
        == "approved"
    )
    revoked_b = _request(
        client,
        api,
        team_owner_h,
        asset_b["id"],
        target="team",
        action="revoke",
        reason="撤下重做",
    )
    assert revoked_b.json()["data"]["status"] == "approved"

    # C：小组审批码 + 我是该组成员 + 撤销本组共享 → 自审
    owner_c, owner_c_h = make_user(
        client, admin, LIB_RW + DIRECT + GROUP_APPROVE
    )
    group_c = make_group(client, admin, [owner_c["id"]])
    asset_c = upload_image(client, api, owner_c_h, color="orange")
    assert (
        _request(
            client, api, owner_c_h, asset_c["id"], group_id=group_c["id"]
        )
        .json()["data"]["status"]
        == "approved"
    )
    revoked_c = _request(
        client,
        api,
        owner_c_h,
        asset_c["id"],
        group_id=group_c["id"],
        action="revoke",
        reason="本组素材下架",
    )
    assert revoked_c.json()["data"]["status"] == "approved"


def test_setting_leaders_still_requires_global_code(
    client: TestClient, api: str
) -> None:
    """负责人表是全站级配置，仍由旧的全站审批码守护，细分码不自动获得写权。"""
    admin = admin_headers(client)
    _, team_h = make_user(client, admin, READ + TEAM_APPROVE)
    _, group_h = make_user(client, admin, READ + GROUP_APPROVE)
    _, all_h = make_user(client, admin, READ + ALL_APPROVE)
    group = make_group(client, admin, [])
    path = f"{api}/library/groups/{group['id']}/leaders"

    assert client.put(path, json={"userIds": []}, headers=team_h).status_code == 403
    assert client.put(path, json={"userIds": []}, headers=group_h).status_code == 403
    assert client.put(path, json={"userIds": []}, headers=all_h).status_code == 200
