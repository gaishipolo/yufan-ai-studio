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
    insert_pack_item,
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
    """GC 只删该删的：无引用 → 物理删除；被封装引用 → 跳过并计入报告。"""
    from library_support import make_session_asset

    from modules.library.maintenance import run_trash_gc

    plain = make_session_asset()
    referenced = make_session_asset()
    insert_pack_item(referenced)
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
