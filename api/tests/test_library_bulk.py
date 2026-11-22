"""标签、批量写、选择器与回收站 GC。

四个断言方向：

1. **批量逐条判定**：一批里已共享的那条失败、其余成功，且返回逐条明细；
2. **选择器硬过滤**：草稿与回收站里的资产永远不出现——这条错了会把用户删掉的
   东西塞进生成任务；
3. **标签是全局词表**：同名不报错、改名撞名 409、删标签不伤资产；
4. **GC 只删该删的**：过了保留期且没人引用的才物理删除，被引用的跳过但计入报告。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    expire_trash,
    fetch_asset,
    insert_asset,
    insert_download_job,
    make_group,
    make_user,
    upload_image,
)


def _bulk(client: TestClient, api: str, headers: dict[str, str], body: dict):
    return client.post(f"{api}/library/assets/bulk", json=body, headers=headers)


def test_bulk_delete_reports_partial_failure(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:share:direct"])
    group = make_group(client, admin, [owner["id"]])

    plain = upload_image(client, api, owner_h, color="lightblue")
    shared = upload_image(client, api, owner_h, color="lightgreen")
    client.post(
        f"{api}/library/share-requests",
        json={"assetId": shared["id"], "target": "group", "groupId": group["id"]},
        headers=owner_h,
    )

    result = _bulk(
        client,
        api,
        owner_h,
        {"ids": [plain["id"], shared["id"]], "action": "delete", "payload": {}},
    )
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["succeeded"] == 1
    assert data["failed"] == 1

    by_id = {item["id"]: item for item in data["results"]}
    assert by_id[plain["id"]]["ok"] is True
    assert by_id[shared["id"]]["ok"] is False
    assert by_id[shared["id"]]["errorCode"] == 40981
    # 逐条判定：成功的那条真的进回收站了，不能因为同批有失败就整体回滚
    assert fetch_asset(plain["id"])["state"] == "trash"  # type: ignore[index]
    assert fetch_asset(shared["id"])["state"] == "active"  # type: ignore[index]


def test_bulk_revoke_rejects_a_stale_version(client: TestClient, api: str) -> None:
    """批量撤销共享的 expected_version 之前没人消费。

    `bulk.py` 把版本传给 `ShareService.request`，而后者只把它原样存进申请单，
    全仓唯一的比较在 `decide()` 里、用的是审批人自己带的那份（前端固定传 0）。
    于是基于旧页面发出的撤销申请会作用在用户没见过的资产状态上——撤销恰恰是
    风险方向最重的一侧（别人正在用的文件突然拿不到）。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:share:direct"])
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, color="lightgreen")

    shared = client.post(
        f"{api}/library/share-requests",
        json={"assetId": asset["id"], "target": "group", "groupId": group["id"]},
        headers=owner_h,
    )
    assert shared.status_code == 201, shared.text

    # 页面上的版本号已经过时（这里用 0 之外的任意值模拟旧页面）
    result = _bulk(
        client,
        api,
        owner_h,
        {
            "ids": [asset["id"]],
            "action": "requestRevoke",
            "payload": {"target": "group", "groupId": group["id"], "reason": "改用另一版"},
            "expectedVersions": {asset["id"]: 999},
        },
    )
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["succeeded"] == 0, "过期的版本号不该放行撤销申请"
    assert data["failed"] == 1
    # 40900 = 框架的通用 Conflict（与 decide() 的版本冲突同一条口径）
    assert data["results"][0]["errorCode"] == 40900


def test_bulk_requires_the_action_permission(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    _, creator_h = make_user(client, admin, ["library:read", "library:create"])
    asset = upload_image(client, api, creator_h, color="tan")

    denied = _bulk(
        client, api, creator_h, {"ids": [asset["id"]], "action": "delete", "payload": {}}
    )
    assert denied.status_code == 403


def test_bulk_add_tag_then_filter_by_tag(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, color="navajowhite")

    tag = client.post(
        f"{api}/library/tags", json={"name": "圣诞活动"}, headers=owner_h
    ).json()["data"]
    # 同名再建返回同一条：标签是词表，不是记录
    again = client.post(f"{api}/library/tags", json={"name": "圣诞活动"}, headers=owner_h)
    assert again.json()["data"]["id"] == tag["id"]

    result = _bulk(
        client,
        api,
        owner_h,
        {"ids": [asset["id"]], "action": "addTag", "payload": {"tagIds": [tag["id"]]}},
    )
    assert result.json()["data"]["succeeded"] == 1

    filtered = client.get(
        f"{api}/library/assets", params={"tagIds": [tag["id"]]}, headers=owner_h
    ).json()["data"]
    assert asset["id"] in [item["id"] for item in filtered["items"]]

    detail = client.get(f"{api}/library/assets/{asset['id']}", headers=owner_h).json()["data"]
    assert [item["name"] for item in detail["tags"]] == ["圣诞活动"]


def test_tag_rename_conflict_and_delete(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:tag:manage"])
    first = client.post(
        f"{api}/library/tags", json={"name": "A 标签"}, headers=owner_h
    ).json()["data"]
    second = client.post(
        f"{api}/library/tags", json={"name": "B 标签"}, headers=owner_h
    ).json()["data"]

    conflict = client.patch(
        f"{api}/library/tags/{first['id']}", json={"name": "B 标签"}, headers=owner_h
    )
    assert conflict.status_code == 409

    renamed = client.patch(
        f"{api}/library/tags/{first['id']}", json={"name": "A 标签改名"}, headers=owner_h
    )
    assert renamed.status_code == 200
    assert renamed.json()["data"]["name"] == "A 标签改名"

    deleted = client.delete(f"{api}/library/tags/{second['id']}", headers=owner_h)
    assert deleted.status_code == 200
    listed = client.get(f"{api}/library/tags", headers=owner_h).json()["data"]
    names = [item["name"] for item in listed]
    assert "B 标签" not in names


def test_selection_never_returns_drafts_or_trash(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    active = upload_image(client, api, owner_h, color="salmon")
    trashed = upload_image(client, api, owner_h, color="slategray")
    client.delete(f"{api}/library/assets/{trashed['id']}", headers=owner_h)
    draft_id = insert_asset(owner_id=owner["id"], name="draft-in-selection.png")

    selection = client.get(
        f"{api}/library/selection", params={"pageSize": 100}, headers=owner_h
    ).json()["data"]
    items = selection["items"]
    ids = [item["id"] for item in items]
    assert active["id"] in ids
    assert trashed["id"] not in ids, "回收站内容不能出现在选择器里"
    assert draft_id not in ids, "草稿未入库，不能出现在选择器里"

    # 列表接口本身按 state 区分，用来确认这不是「列表也错了」
    drafts = client.get(
        f"{api}/library/assets", params={"state": "draft"}, headers=owner_h
    ).json()["data"]
    assert draft_id in [item["id"] for item in drafts["items"]]


def test_trash_gc_purges_expired_and_skips_referenced() -> None:
    """GC 只删该删的：无引用 → 物理删除；被下载任务引用 → 跳过并计入报告。"""
    from library_support import make_session_asset

    from modules.library.maintenance import run_trash_gc

    plain = make_session_asset()
    referenced = make_session_asset()
    insert_download_job(referenced)
    expire_trash(plain)
    expire_trash(referenced)

    report = asyncio.run(run_trash_gc(batch=50))
    assert report.scanned >= 2
    assert fetch_asset(plain) is None, "没人引用且已过期的条目必须被清掉"
    assert report.purged >= 1
    assert referenced in report.blocked
    assert fetch_asset(referenced) is not None, "被引用的条目必须留下，不能静默删掉"


def test_trash_gc_dry_run_deletes_nothing() -> None:
    from library_support import make_session_asset

    from modules.library.maintenance import run_trash_gc

    asset_id = make_session_asset()
    expire_trash(asset_id)

    report = asyncio.run(run_trash_gc(batch=50, dry_run=True))
    assert report.dry_run is True
    assert report.purged == 0
    assert fetch_asset(asset_id) is not None


def test_bulk_share_reports_partial_failure(client: TestClient, api: str) -> None:
    """一批里已经共享过的那条失败，其余照样生效，并带出逐条明细。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:share:direct"])

    plain = upload_image(client, api, owner_h, color="lightcoral")
    already = upload_image(client, api, owner_h, color="lightsalmon")
    first = client.post(
        f"{api}/library/share-requests",
        json={"assetId": already["id"], "target": "team", "groupId": ""},
        headers=owner_h,
    )
    assert first.status_code == 201, first.text

    result = _bulk(
        client,
        api,
        owner_h,
        {
            "ids": [plain["id"], already["id"]],
            "action": "requestShare",
            "payload": {"target": "team", "reason": "给全员看"},
        },
    )
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert (data["succeeded"], data["failed"]) == (1, 1)

    by_id = {item["id"]: item for item in data["results"]}
    assert by_id[plain["id"]]["ok"] is True
    assert by_id[already["id"]]["ok"] is False
    assert "已共享给团队" in by_id[already["id"]]["message"]
    assert fetch_asset(plain["id"])["visibility"] == "team"
    assert fetch_asset(already["id"])["visibility"] == "team"


def test_bulk_share_by_non_owner_stays_pending(client: TestClient, api: str) -> None:
    """批量里混着别人的素材：看得见就能申请，但不许直接生效。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:share:direct"])
    other, other_h = make_user(client, admin, LIB_RW)
    group = make_group(client, admin, [owner["id"], other["id"]])

    ids = []
    for color in ("lightyellow", "lightpink"):
        asset = upload_image(client, api, owner_h, color=color)
        ids.append(asset["id"])
        resp = client.post(
            f"{api}/library/share-requests",
            json={"assetId": asset["id"], "target": "group", "groupId": group["id"]},
            headers=owner_h,
        )
        assert resp.status_code == 201, resp.text

    result = _bulk(
        client,
        api,
        other_h,
        {"ids": ids, "action": "requestShare", "payload": {"target": "team"}},
    )
    assert result.status_code == 200, result.text
    assert result.json()["data"]["succeeded"] == 2
    # 审批没通过之前可见范围不能变
    assert all(fetch_asset(asset_id)["visibility"] == "group" for asset_id in ids)

    # `scope=mine` 才是「我提交的」；`pending` 是「待我审核」，按审批权限收窄
    mine = client.get(f"{api}/library/share-requests", headers=other_h)
    assert mine.status_code == 200, mine.text
    mine_ids = {
        item["assetId"]
        for item in mine.json()["data"]
        if item["action"] == "share" and item["status"] == "pending"
    }
    assert set(ids) <= mine_ids


def test_bulk_share_honours_each_expected_version(client: TestClient, api: str) -> None:
    """版本号逐条校验：旧页面发出的一批不能把新状态盖掉。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:share:direct"])

    stale = upload_image(client, api, owner_h, color="lightgreen")
    fresh = upload_image(client, api, owner_h, color="lightblue")
    result = _bulk(
        client,
        api,
        owner_h,
        {
            "ids": [stale["id"], fresh["id"]],
            "action": "requestShare",
            "payload": {"target": "team"},
            "expectedVersions": {
                stale["id"]: 999,
                fresh["id"]: fetch_asset(fresh["id"])["state_version"],
            },
        },
    )
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert (data["succeeded"], data["failed"]) == (1, 1)
    by_id = {item["id"]: item for item in data["results"]}
    assert "已被修改" in by_id[stale["id"]]["message"]
    assert fetch_asset(stale["id"])["visibility"] == "personal"
    assert fetch_asset(fresh["id"])["visibility"] == "team"
