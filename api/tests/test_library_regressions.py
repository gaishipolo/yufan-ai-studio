"""资产库 v2 的跨模块回归：每条都对应一次「读代码时没看出来、跑一下才现形」的破绽。

不按模块拆进 `test_library_bulk/lifecycle/downloads/upload`，是因为它们守的是
**同一类东西**：同一条规则在两处实现，其中一处漏了一半。

| 用例 | 守的不变式 |
|---|---|
| `bulk_add_tag_checks_visibility` | 写标签和改名一样，先判「看得见且是我的」 |
| `purge_keeps_blob_shared_with_other_trash_rows` | 「还有谁引用字节」不带 `deleted_at` |
| `facets_match_group_list` | 角标 = 点进去的条数（同一个入口口径） |
| `selection_total_respects_kind_filter` | 筛选条件必须在 SQL 里，否则 total 虚高 |
| `upload_source_key_is_owner_scoped` | 客户端给的幂等键没有跨用户语义 |
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    make_group,
    make_user,
    png_bytes,
    set_members,
    upload_image,
)

SHARE_DIRECT = [*LIB_RW, "library:share:direct"]


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


def test_selection_total_respects_kind_filter(
    client: TestClient, api: str
) -> None:
    """筛选条件必须落在 SQL 里」这条不变式原来用已下线的素材封装做例子：
    封装下线后那条用例空跑——参数早就不存在了，FastAPI 静默忽略未知参数，
    断言恒真。改用真实存在的维度：类别。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    upload_image(client, api, owner_h, name="sel-a.png", color="slateblue")
    text = client.post(
        f"{api}/library/text-assets",
        json={"name": "sel-b.md", "content": "正文", "extension": "md"},
        headers=owner_h,
    )
    assert text.status_code == 201, text.text

    every = client.get(
        f"{api}/library/selection", params={"pageSize": 100}, headers=owner_h
    ).json()["data"]
    page = client.get(
        f"{api}/library/selection",
        params={"kind": "text", "pageSize": 100},
        headers=owner_h,
    ).json()["data"]
    assert page["total"] == 1, f"筛选后 total 应该是 1，实际 {page['total']}"
    assert page["total"] == len(page["items"]), (
        f"total={page['total']} 但只返回 {len(page['items'])} 条"
    )
    assert page["total"] < every["total"]


def test_owner_keeps_seeing_a_shared_asset_after_leaving_the_group(
    client: TestClient, api: str
) -> None:
    """本人被移出小组后，自己的共享素材不能从界面上彻底消失。

    `can_read` 的 owner 分支无条件放行（共享出去之后仍是「我的」），而 SQL 分支
    要求 `visibility == "personal"`：同一条规则写了两遍、漂了一支。于是素材在
    四个入口里全部不见了，角标也不计数，只剩直链能打开——用户自己的东西，也
    没法自救（撤销共享不再是自审）。

    SQL 分支的口径必须与 `can_read` 对齐：本人可见，但只在**别处看不到**的时候
    回个人库显示（共享给某个组、我还在那个组里 → 小组库显示，不重复）。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, SHARE_DIRECT)
    other, other_h = make_user(client, admin, SHARE_DIRECT)
    group = make_group(client, admin, [owner["id"], other["id"]])
    asset = upload_image(client, api, owner_h, name="mine.png", color="navy")

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
    assert shared.json()["data"]["status"] == "approved"

    # 我被移出小组
    set_members(client, admin, group["id"], [other["id"]])

    # 直链能开（can_read 放行）
    assert client.get(f"{api}/library/assets/{asset['id']}", headers=owner_h).status_code == 200

    # 列表的两个入口也要能看到它
    for tab in ("all", "personal"):
        page = client.get(
            f"{api}/library/assets",
            params={"library": tab, "pageSize": 100},
            headers=owner_h,
        ).json()["data"]
        assert asset["id"] in [item["id"] for item in page["items"]], (
            f"{tab} 入口里看不到自己的素材：被移出小组后不该凭空消失"
        )

    # 角标与列表必须对得上
    facets = client.get(f"{api}/library/facets", headers=owner_h).json()["data"]
    personal = client.get(
        f"{api}/library/assets",
        params={"library": "personal", "pageSize": 100},
        headers=owner_h,
    ).json()["data"]
    assert facets["personal"] == personal["total"]


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


