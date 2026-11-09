"""生命周期与删除矩阵（设计 §3.3 / §3.4 / §3.4.1）。

这一组用例对应「删不掉 / 删错了」这两类真实事故：

- **删不掉**：已共享的资产 409，但用户不知道该先撤销共享 → 响应里必须带
  `canRevokeAndDelete`；组长走窄口时必须**一次事务**落地，且留一条自审申请单；
- **删错了**：团队库资产任何人不可删（含上传者）；彻底删除前必须过引用检查，
  被引用时列出「谁在用」而不是静默跳过。

不变式 A / B（草稿与回收站必须 personal、回收站不得留组行）在这组用例里各钉一条。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    fetch_asset,
    fetch_group_rows,
    fetch_share_requests,
    insert_asset,
    insert_pack_item,
    make_group,
    make_user,
    upload_image,
)

SHARE_PERMISSIONS = LIB_RW
REVIEWER_PERMISSIONS = ["library:read", "library:create", "library:draft:review"]


def _approve_share(client: TestClient, api: str, admin: dict[str, str], request_id: str) -> None:
    response = client.post(
        f"{api}/library/share-requests/{request_id}/approve", json={}, headers=admin
    )
    assert response.status_code == 200, response.text


def test_owner_can_self_approve_own_draft(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset_id = insert_asset(owner_id=owner["id"], name="draft-self.png")

    response = client.post(
        f"{api}/library/drafts/{asset_id}/approve", json={}, headers=owner_h
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "active"
    assert response.json()["data"]["visibility"] == "personal"


def test_reviewing_someone_elses_draft_requires_the_permission(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, _ = make_user(client, admin, LIB_RW)
    stranger, stranger_h = make_user(client, admin, LIB_RW)
    asset_id = insert_asset(owner_id=owner["id"])

    denied = client.post(
        f"{api}/library/drafts/{asset_id}/approve", json={}, headers=stranger_h
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == 40381

    reviewer, reviewer_h = make_user(client, admin, REVIEWER_PERMISSIONS)
    approved = client.post(
        f"{api}/library/drafts/{asset_id}/approve",
        json={"reason": "可用"},
        headers=reviewer_h,
    )
    assert approved.status_code == 200, approved.text
    # 代审后归 owner，不归审核人
    assert approved.json()["data"]["ownerId"] == owner["id"]


def test_rejected_draft_restores_back_to_draft_not_to_library(
    client: TestClient, api: str
) -> None:
    """驳回 = 进回收站；恢复必须回到草稿箱，否则「审核」这一步被绕过。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset_id = insert_asset(owner_id=owner["id"], name="draft-reject.png")

    rejected = client.post(
        f"{api}/library/drafts/{asset_id}/reject",
        json={"reason": "构图太乱"},
        headers=owner_h,
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["state"] == "trash"
    row = fetch_asset(asset_id)
    assert row is not None
    assert row["trash_reason"] == "rejected"
    assert row["trashed_from_state"] == "draft"
    assert row["visibility"] == "personal"

    restored = client.post(f"{api}/library/trash/{asset_id}/restore", headers=owner_h)
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["state"] == "draft"


def test_purge_removes_the_row_and_the_file(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, color="indigo")

    deleted = client.delete(f"{api}/library/assets/{asset['id']}", headers=owner_h)
    assert deleted.status_code == 200

    # 回收站里的条目不再提供预览与下载（否则「删除」只是个标签）
    served = client.get(f"{api}/library/assets/{asset['id']}/file", headers=owner_h)
    assert served.status_code == 404

    purged = client.delete(f"{api}/library/trash/{asset['id']}", headers=owner_h)
    assert purged.status_code == 200, purged.text
    assert fetch_asset(asset["id"]) is None
    assert client.get(f"{api}/library/assets/{asset['id']}", headers=owner_h).status_code == 404


def test_purge_is_blocked_while_referenced(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, color="coral")
    client.delete(f"{api}/library/assets/{asset['id']}", headers=owner_h)
    insert_pack_item(asset["id"])

    blocked = client.delete(f"{api}/library/trash/{asset['id']}", headers=owner_h)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == 40986
    assert blocked.json()["details"]["references"], "必须说清「谁在引用」，否则用户只能猜"


def test_delete_on_a_trashed_entry_is_a_conflict(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, color="khaki")
    client.delete(f"{api}/library/assets/{asset['id']}", headers=owner_h)

    again = client.delete(f"{api}/library/assets/{asset['id']}", headers=owner_h)
    assert again.status_code == 409

    not_in_trash = client.delete(f"{api}/library/trash/{asset['id']}", headers=owner_h)
    assert not_in_trash.status_code == 200
    # 已经彻底删掉，再调 restore 是 404（行都不在了）
    assert (
        client.post(f"{api}/library/trash/{asset['id']}/restore", headers=owner_h).status_code
        == 404
    )


def test_leader_narrow_gate_deletes_in_one_step_with_an_audit_record(
    client: TestClient, api: str
) -> None:
    """设计 §3.4.1：`owner=me ∧ 只共享给本组 ∧ 我是组长` → 撤销 + 删除一步完成。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS + ["library:share:direct"])
    group = make_group(client, admin, [owner["id"]])
    client.put(
        f"{api}/library/groups/{group['id']}/leaders",
        json={"userIds": [owner["id"]]},
        headers=admin,
    )
    asset = upload_image(client, api, owner_h, color="plum")

    shared = client.post(
        f"{api}/library/share-requests",
        json={"assetId": asset["id"], "target": "group", "groupId": group["id"]},
        headers=owner_h,
    ).json()["data"]
    assert shared["status"] == "approved"

    # 不传开关：409，但告诉前端「你可以一步走」
    blocked = client.delete(f"{api}/library/assets/{asset['id']}", headers=owner_h)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == 40981
    assert blocked.json()["details"]["canRevokeAndDelete"] is True

    # 但缺理由时仍然拒绝：撤销共享必须留理由
    no_reason = client.delete(
        f"{api}/library/assets/{asset['id']}",
        params={"revokeOwnGroupShare": "true"},
        headers=owner_h,
    )
    assert no_reason.status_code == 400
    assert no_reason.json()["code"] == 40081

    done = client.delete(
        f"{api}/library/assets/{asset['id']}",
        params={"revokeOwnGroupShare": "true", "reason": "本组用完，清理"},
        headers=owner_h,
    )
    assert done.status_code == 200, done.text

    row = fetch_asset(asset["id"])
    assert row is not None
    assert row["state"] == "trash"
    assert row["visibility"] == "personal"
    # 不变式 B：回收站里不允许残留组行
    assert fetch_group_rows(asset["id"]) == []

    # 一步不等于不记录：必须留一条自审的已通过申请单
    requests = fetch_share_requests(asset["id"])
    combined = [item for item in requests if item["note"] == "self_leader_combined"]
    assert len(combined) == 1
    assert combined[0]["status"] == "approved"
    assert combined[0]["reviewer_id"] == owner["id"]
    assert combined[0]["action"] == "revoke"


def test_narrow_gate_does_not_apply_when_shared_to_two_groups(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS + ["library:share:direct"])
    group_a = make_group(client, admin, [owner["id"]])
    group_b = make_group(client, admin, [owner["id"]])
    client.put(
        f"{api}/library/groups/{group_a['id']}/leaders",
        json={"userIds": [owner["id"]]},
        headers=admin,
    )
    asset = upload_image(client, api, owner_h, color="orchid")
    for group in (group_a, group_b):
        response = client.post(
            f"{api}/library/share-requests",
            json={"assetId": asset["id"], "target": "group", "groupId": group["id"]},
            headers=owner_h,
        )
        assert response.status_code == 201, response.text

    blocked = client.delete(
        f"{api}/library/assets/{asset['id']}",
        params={"revokeOwnGroupShare": "true", "reason": "试试"},
        headers=owner_h,
    )
    assert blocked.status_code == 409
    assert blocked.json()["details"]["canRevokeAndDelete"] is False, (
        "共享给多个组时会影响本组之外的人，必须走完整撤销审批"
    )


def test_admin_delete_of_shared_asset_requires_reason_and_records_self_review(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_PERMISSIONS + ["library:share:direct"])
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, color="steelblue")
    client.post(
        f"{api}/library/share-requests",
        json={"assetId": asset["id"], "target": "group", "groupId": group["id"]},
        headers=owner_h,
    )

    missing_reason = client.delete(f"{api}/library/assets/{asset['id']}", headers=admin)
    assert missing_reason.status_code == 400

    done = client.delete(
        f"{api}/library/assets/{asset['id']}",
        params={"reason": "违规素材"},
        headers=admin,
    )
    assert done.status_code == 200, done.text
    row = fetch_asset(asset["id"])
    assert row is not None and row["state"] == "trash"
    assert fetch_group_rows(asset["id"]) == []
    forced = [item for item in fetch_share_requests(asset["id"]) if item["note"] == "admin_forced"]
    assert forced and forced[0]["status"] == "approved"
