"""资产库 v2 的跨模块回归：每条都对应一次「读代码时没看出来、跑一下才现形」的破绽。

不按模块拆进 `test_library_bulk/lifecycle/downloads/upload`，是因为它们守的是
**同一类东西**：同一条规则在两处实现，其中一处漏了一半。

| 用例 | 守的不变式 |
|---|---|
| `bulk_add_tag_checks_visibility` | 写标签和改名一样，先判「看得见且是我的」 |
| `purge_keeps_blob_shared_with_other_trash_rows` | 「还有谁引用字节」不带 `deleted_at` |
| `facets_match_group_list` | 角标 = 点进去的条数（同一个入口口径） |
| `selection_total_respects_include_packs` | 排除条件必须在 SQL 里，否则 total 虚高 |
| `trashed_pack_is_not_downloadable` | 解散 = 不可下载（封装没有 storage_key，不能只看它） |
| `upload_source_key_is_owner_scoped` | 客户端给的幂等键没有跨用户语义 |
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    fetch_asset,
    make_group,
    make_user,
    png_bytes,
    upload_image,
)

SHARE_DIRECT = [*LIB_RW, "library:share:direct"]


def _create_pack(client: TestClient, api: str, headers: dict, body: dict):
    return client.post(f"{api}/library/packs", json=body, headers=headers)


def test_bulk_add_tag_checks_visibility(client: TestClient, api: str) -> None:
    """批量打标签也要像改名一样先判「这条我看得见 / 是我的」。

    对照 `LibraryService.patch`：改名走 `load_visible` + owner 判定。
    批量 `addTag` 直接调 `add_asset_tags`，没有这一步。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    intruder, intruder_h = make_user(client, admin, LIB_RW)

    asset = upload_image(client, api, owner_h, name="private.png", color="maroon")
    tag = client.post(
        f"{api}/library/tags", json={"name": "intruder"}, headers=intruder_h
    )
    assert tag.status_code == 201, tag.text
    tag_id = tag.json()["data"]["id"]

    response = client.post(
        f"{api}/library/assets/bulk",
        json={"ids": [asset["id"]], "action": "addTag", "payload": {"tagIds": [tag_id]}},
        headers=intruder_h,
    )
    assert response.status_code == 200, response.text
    result = response.json()["data"]

    # 期望：看不见的资产不能被别人打标签
    assert result["succeeded"] == 0, f"越权打标签成功了：{result}"
    detail = client.get(f"{api}/library/assets/{asset['id']}", headers=owner_h).json()["data"]
    assert detail["tags"] == [], f"别人的标签被写进来了：{detail['tags']}"


def test_purge_keeps_blob_shared_with_other_trash_rows(
    client: TestClient, api: str
) -> None:
    """两份同内容资产都在回收站时，先彻底删除的那条不能删掉物理文件。

    `_purge_row` 的 others 计数带了 `deleted_at IS NULL`，而回收站行的
    `deleted_at` 恰好非空——于是它认为「没有别人在用」，把字节删了。
    """
    admin = admin_headers(client)
    first, first_h = make_user(client, admin, LIB_RW)
    second, second_h = make_user(client, admin, LIB_RW)

    # 同一份字节：内容寻址后 storage_key 相同，物理上只有一份
    a = upload_image(client, api, first_h, name="same-a.png", color="chocolate")
    b = upload_image(client, api, second_h, name="same-b.png", color="chocolate")

    assert client.delete(f"{api}/library/assets/{a['id']}", headers=first_h).status_code == 200
    assert client.delete(f"{api}/library/assets/{b['id']}", headers=second_h).status_code == 200

    # A 彻底删除
    assert client.delete(f"{api}/library/trash/{a['id']}", headers=first_h).status_code == 200

    # B 还在回收站里，恢复之后原件必须还在
    restored = client.post(f"{api}/library/trash/{b['id']}/restore", headers=second_h)
    assert restored.status_code == 200, restored.text
    served = client.get(f"{api}/library/assets/{b['id']}/file", headers=second_h)
    assert served.status_code == 200, (
        f"B 的字节被 A 的彻底删除带走了：status={served.status_code}"
    )


def test_facets_match_group_list(client: TestClient, api: str) -> None:
    """分面角标必须和点进去看到的条数一致。

    列表按入口口径判「封装对我可见」（group 入口只认共享到组的封装），
    分面却用全量可见集 —— 我把团队/组素材装进**自己的个人封装**时，
    列表不隐藏、分面隐藏，角标就少了。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_DIRECT)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h, name="group-shared.png", color="teal")

    shared = client.post(
        f"{api}/library/share-requests",
        json={
            "assetId": asset["id"],
            "action": "share",
            "target": "group",
            "groupId": group["id"],
        },
        headers=owner_h,
    )
    assert shared.status_code == 201, shared.text
    assert shared.json()["data"]["status"] == "approved", shared.text

    # 装进**自己的个人封装**：小组库仍该看到这条素材
    _create_pack(
        client, api, owner_h, {"name": "我的私人封装", "assetIds": [asset["id"]]}
    )

    listed = client.get(
        f"{api}/library/assets",
        params={"library": "group", "state": "active", "pageSize": 100},
        headers=owner_h,
    ).json()["data"]
    facets = client.get(f"{api}/library/facets", headers=owner_h).json()["data"]

    assert asset["id"] in [item["id"] for item in listed["items"]], "列表里该有这条"
    assert facets["group"] == listed["total"], (
        f"角标 {facets['group']} ≠ 列表 {listed['total']}：点进去数目对不上"
    )


def test_selection_total_respects_include_packs(
    client: TestClient, api: str
) -> None:
    """`includePacks=false` 时 total 也要跟着减，否则分页会少一页。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, name="sel-a.png", color="slateblue")
    upload_image(client, api, owner_h, name="sel-b.png", color="thistle")
    _create_pack(client, api, owner_h, {"name": "选择器封装", "assetIds": [first["id"]]})

    page = client.get(
        f"{api}/library/selection",
        params={"includePacks": "false", "pageSize": 100},
        headers=owner_h,
    ).json()["data"]
    assert all(item["kind"] != "pack" for item in page["items"])
    assert page["total"] == len(page["items"]), (
        f"total={page['total']} 但只返回 {len(page['items'])} 条"
    )


def test_trashed_pack_is_not_downloadable(client: TestClient, api: str) -> None:
    """解散进回收站的封装不该还能被打包。

    `_collect` 对 `kind=pack` 直接 `continue` 展开成员，跳过了 `_skip_reason`
    里「回收站内容不可下载」那一条。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, name="trash-pack-a.png", color="olive")
    second = upload_image(client, api, owner_h, name="trash-pack-b.png", color="olive")
    pack = _create_pack(
        client, api, owner_h, {"name": "待解散", "assetIds": [first["id"], second["id"]]}
    ).json()["data"]

    assert client.delete(f"{api}/library/packs/{pack['id']}", headers=owner_h).status_code == 200
    assert fetch_asset(pack["id"])["state"] == "trash"

    job = client.post(
        f"{api}/library/downloads", json={"ids": [pack["id"]]}, headers=owner_h
    )
    assert job.status_code in (400, 413), (
        f"回收站里的封装仍能发起打包：{job.status_code} {job.text}"
    )


def test_upload_source_key_is_owner_scoped(client: TestClient, api: str) -> None:
    """同一个 `sourceKey` 被两个用户使用时，不该把后者的文件吞掉。

    `uq_library_assets_source_key` 是全局唯一，`_by_source_key` 也不带 owner，
    而 `?sourceKey=` 是客户端可传的。
    """
    admin = admin_headers(client)
    first, first_h = make_user(client, admin, LIB_RW)
    second, second_h = make_user(client, admin, LIB_RW)

    upload_first = client.post(
        f"{api}/library/assets/upload",
        content=png_bytes("navy"),
        params={"filename": "k-a.png", "kind": "image", "sourceKey": "shared-key"},
        headers=first_h,
    )
    assert upload_first.status_code == 201, upload_first.text

    upload_second = client.post(
        f"{api}/library/assets/upload",
        content=png_bytes("gold"),
        params={"filename": "mine.png", "kind": "image", "sourceKey": "shared-key"},
        headers=second_h,
    )
    assert upload_second.status_code in (200, 201), upload_second.text
    body = upload_second.json()["data"]
    assert body["ownerId"] == second["id"], (
        f"同一个 sourceKey 把 A 的资产返回给了 B：id={body['id']} owner={body['ownerId']}"
    )


def test_dissolved_pack_does_not_block_member_purge(
    client: TestClient, api: str
) -> None:
    """解散的封装不该继续「保护」成员——否则成员永远删不掉。

    引用检查原先不区分封装的 state：解散后 `library_pack_items` 的行还在
    （恢复时要原样回来），于是彻底删除成员会被挡在「封装 X 的成员」上，
    而 X 正是用户刚解散的那个，界面上已经找不到它。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    member = upload_image(client, api, owner_h, name="packed-member.png", color="sienna")
    pack = _create_pack(
        client, api, owner_h, {"name": "引用检查", "assetIds": [member["id"]]}
    ).json()["data"]

    assert client.delete(f"{api}/library/assets/{member['id']}", headers=owner_h).status_code == 200

    # 封装在库：成员仍被引用，彻底删除要被拦住
    blocked = client.delete(f"{api}/library/trash/{member['id']}", headers=owner_h)
    assert blocked.status_code == 409, f"在库封装的成员被直接删掉了：{blocked.text}"

    # 解散封装之后再删：此时没有任何在库的东西在用它
    assert client.delete(f"{api}/library/packs/{pack['id']}", headers=owner_h).status_code == 200
    purged = client.delete(f"{api}/library/trash/{member['id']}", headers=owner_h)
    assert purged.status_code == 200, f"解散封装后成员仍删不掉：{purged.text}"
    assert fetch_asset(member["id"]) is None
