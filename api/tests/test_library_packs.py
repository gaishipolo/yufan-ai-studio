"""封装（pack）：建、改、解散、彻底删除，以及批量「勾选一批就封装」。

五个断言方向：

1. **成员顺序与统计**：`packItems` 按 `position` 返回，`sizeBytes` 是成员之和；
2. **成员必须可用**：看不见的、不在库的、另一个封装都不许进来（否则下载解包
   会变成递归，或者变成窥探别人资产的探针）；
3. **改封装是整体替换**：`items` 给全量，位置唯一索引不能被旧行挡住；
4. **解散不动成员**：封装进回收站，成员仍是 `active`；
5. **彻底删除封装要清掉成员关系**：`library_pack_items` 是按 `pack_id` 挂的，
   漏了这段会留下孤儿行，而引用检查只按 `asset_id` 查，谁也不会发现。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    fetch_asset,
    fetch_pack_items,
    insert_asset,
    make_user,
    upload_image,
)


def _create_pack(
    client: TestClient, api: str, headers: dict[str, str], body: dict
):
    return client.post(f"{api}/library/packs", json=body, headers=headers)


def test_create_pack_keeps_member_order_and_size(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, name="a.png", color="orchid")
    second = upload_image(client, api, owner_h, name="b.png", color="olive")

    response = _create_pack(
        client,
        api,
        owner_h,
        {
            "name": "产品三视图",
            "items": [
                {"assetId": second["id"], "role": "主图", "description": "正面"},
                {"assetId": first["id"], "role": "结构图"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    pack = response.json()["data"]
    assert pack["kind"] == "pack"
    assert pack["origin"] == "pack"
    assert pack["state"] == "active"
    assert pack["visibility"] == "personal"

    # items 的顺序就是压缩包里的顺序，不能按 id 排序（用户拖过顺序）
    assert [item["assetId"] for item in pack["packItems"]] == [second["id"], first["id"]]
    assert [item["position"] for item in pack["packItems"]] == [1, 2]
    assert pack["packItems"][0]["role"] == "主图"
    assert pack["sizeBytes"] == first["sizeBytes"] + second["sizeBytes"]
    assert pack["coverUrl"], "有图片成员时必须自动取一张封面"


def test_pack_rejects_nested_pack_and_invisible_member(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    other, other_h = make_user(client, admin, LIB_RW)
    mine = upload_image(client, api, owner_h, color="peru")
    theirs = upload_image(client, api, other_h, color="plum")

    inner = _create_pack(
        client, api, owner_h, {"name": "内层", "assetIds": [mine["id"]]}
    ).json()["data"]

    nested = _create_pack(client, api, owner_h, {"name": "外层", "assetIds": [inner["id"]]})
    assert nested.status_code == 400
    assert nested.json()["code"] == 40083
    assert nested.json()["details"]["items"] == [inner["id"]]

    foreign = _create_pack(client, api, owner_h, {"name": "偷看", "assetIds": [theirs["id"]]})
    assert foreign.status_code == 400
    assert foreign.json()["details"]["items"] == [theirs["id"]]

    empty = _create_pack(client, api, owner_h, {"name": "空的", "assetIds": []})
    assert empty.status_code == 400
    assert empty.json()["code"] == 40084


def test_patch_pack_replaces_members_and_keeps_versions(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, name="p1.png", color="sienna")
    second = upload_image(client, api, owner_h, name="p2.png", color="silver")
    pack = _create_pack(
        client, api, owner_h, {"name": "可改", "assetIds": [first["id"], second["id"]]}
    ).json()["data"]

    renamed = client.patch(
        f"{api}/library/packs/{pack['id']}",
        json={"name": "改名后", "items": [{"assetId": second["id"], "role": "唯一"}]},
        headers=owner_h,
    )
    assert renamed.status_code == 200, renamed.text
    updated = renamed.json()["data"]
    assert updated["name"] == "改名后"
    assert [item["assetId"] for item in updated["packItems"]] == [second["id"]]
    assert updated["sizeBytes"] == second["sizeBytes"]
    assert updated["stateVersion"] == pack["stateVersion"] + 1

    # 位置唯一索引 `(pack_id, position)`：整体替换后旧行必须真的没了
    assert len(fetch_pack_items(pack["id"])) == 1

    stale = client.patch(
        f"{api}/library/packs/{pack['id']}",
        json={"name": "旧版本提交", "expectedVersion": pack["stateVersion"]},
        headers=owner_h,
    )
    assert stale.status_code == 409


def test_dissolve_pack_keeps_members_active(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    member = upload_image(client, api, owner_h, color="thistle")
    pack = _create_pack(
        client, api, owner_h, {"name": "要解散", "assetIds": [member["id"]]}
    ).json()["data"]

    dissolved = client.delete(f"{api}/library/packs/{pack['id']}", headers=owner_h)
    assert dissolved.status_code == 200, dissolved.text
    assert fetch_asset(pack["id"])["state"] == "trash"  # type: ignore[index]
    assert fetch_asset(member["id"])["state"] == "active"  # type: ignore[index]

    # 成员仍在，点开详情依旧可见；封装在回收站里也能被所有者看见
    detail = client.get(f"{api}/library/assets/{member['id']}", headers=owner_h)
    assert detail.status_code == 200
    trash = client.get(
        f"{api}/library/assets", params={"state": "trash"}, headers=owner_h
    ).json()["data"]
    assert pack["id"] in [item["id"] for item in trash["items"]]


def test_purge_pack_removes_member_rows(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    member = upload_image(client, api, owner_h, color="wheat")
    pack = _create_pack(
        client, api, owner_h, {"name": "要彻底删", "assetIds": [member["id"]]}
    ).json()["data"]
    client.delete(f"{api}/library/packs/{pack['id']}", headers=owner_h)

    purged = client.delete(f"{api}/library/trash/{pack['id']}", headers=owner_h)
    assert purged.status_code == 200, purged.text
    assert fetch_pack_items(pack["id"]) == []
    assert fetch_asset(member["id"])["state"] == "active"  # type: ignore[index]


def test_member_detail_explains_why_it_cannot_be_purged(
    client: TestClient, api: str
) -> None:
    """「使用与引用」是「为什么删不掉」的唯一解释来源。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    member = upload_image(client, api, owner_h, color="khaki")
    pack = _create_pack(
        client, api, owner_h, {"name": "引用来源", "assetIds": [member["id"]]}
    ).json()["data"]

    detail = client.get(f"{api}/library/assets/{member['id']}", headers=owner_h).json()["data"]
    assert detail["references"] == [f"封装 {pack['id']} 的成员"]
    assert detail["archive"]["referenceCount"] == 1

    client.delete(f"{api}/library/assets/{member['id']}", headers=owner_h)
    blocked = client.delete(f"{api}/library/trash/{member['id']}", headers=owner_h)
    assert blocked.status_code == 409
    assert pack["id"] in blocked.json()["details"]["references"][0]


def test_bulk_pack_creates_one_pack_for_the_batch(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, color="navy")
    second = upload_image(client, api, owner_h, color="teal")

    result = client.post(
        f"{api}/library/assets/bulk",
        json={
            "ids": [first["id"], second["id"]],
            "action": "pack",
            "payload": {"name": "批量封装"},
        },
        headers=owner_h,
    )
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["succeeded"] == 1, "pack 是整批建一个，不是每条约一个"
    pack_id = data["results"][0]["id"]
    detail = client.get(f"{api}/library/packs/{pack_id}", headers=owner_h).json()["data"]
    assert len(detail["packItems"]) == 2
    assert detail["name"] == "批量封装"


# --------------------------------------------------------------------------- #
# 封装在列表里「占一个位置」：成员不再单列
# --------------------------------------------------------------------------- #
#
# 用户看到问题是：封了 3 个素材，列表里变成 4 条（1 个封装 + 3 个原素材），
# 看起来像封装「又复制了一份」。下面这几条把口径钉在 SQL 上。


def _list_ids(
    client: TestClient,
    api: str,
    headers: dict[str, str],
    *,
    library: str = "personal",
    kind: str | None = None,
) -> list[str]:
    params: dict[str, object] = {"library": library, "state": "active", "pageSize": 100}
    if kind:
        params["kind"] = kind
    response = client.get(f"{api}/library/assets", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["data"]["items"]]


def test_packed_members_are_not_listed_separately(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, name="pack-a.png", color="orchid")
    second = upload_image(client, api, owner_h, name="pack-b.png", color="olive")
    third = upload_image(client, api, owner_h, name="pack-c.png", color="coral")

    before = _list_ids(client, api, owner_h)
    assert {first["id"], second["id"], third["id"]} <= set(before)

    pack = _create_pack(
        client,
        api,
        owner_h,
        {"name": "三视图", "assetIds": [first["id"], second["id"], third["id"]]},
    ).json()["data"]

    after = _list_ids(client, api, owner_h)
    assert len(after) == len(before) - 2, "3 条成员 + 1 个封装 = 原来条数 - 2"
    assert pack["id"] in after
    assert not {first["id"], second["id"], third["id"]} & set(after)

    # 角标与列表同口径，否则「个人 4」点进去只看到 1 条
    facets = client.get(f"{api}/library/facets", headers=owner_h).json()["data"]
    assert facets["personal"] == len(after)


def test_members_come_back_after_pack_is_dissolved(client: TestClient, api: str) -> None:
    """解散 = 封装进回收站，成员一行不动，并且要**立刻回到列表**。

    隐藏是「按封装关系算出来的」而不是给成员打了标记，所以封装离开 active
    的那一刻，成员自然重新出现——这条用例守的就是这个「自然」。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, name="dissolve-a.png", color="navy")
    second = upload_image(client, api, owner_h, name="dissolve-b.png", color="teal")
    pack = _create_pack(
        client, api, owner_h, {"name": "待解散", "assetIds": [first["id"], second["id"]]}
    ).json()["data"]

    hidden = _list_ids(client, api, owner_h)
    assert not {first["id"], second["id"]} & set(hidden)

    assert client.delete(f"{api}/library/packs/{pack['id']}", headers=owner_h).status_code == 200

    back = _list_ids(client, api, owner_h)
    assert {first["id"], second["id"]} <= set(back)
    assert pack["id"] not in back


def test_member_shared_to_team_stays_visible_there(client: TestClient, api: str) -> None:
    """隐藏必须限定在「封装对我可见」的入口里。

    一条素材共享到团队后，被我装进**自己的个人封装**：团队成员仍要在团队库
    看到它。只判 `library_pack_items` 有行的话，别人会以为素材被删了。

    团队那条用 `insert_asset` 直接造（PATCH 不含可见性：变更一律走申请单）。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    mine = upload_image(client, api, owner_h, name="team-mine.png", color="khaki")
    shared_id = insert_asset(
        owner_id=owner["id"],
        name="team-shared.png",
        origin="upload",
        state="active",
        visibility="team",
    )

    pack = _create_pack(
        client, api, owner_h, {"name": "个人封装", "assetIds": [mine["id"], shared_id]}
    )
    assert pack.status_code == 201, pack.text

    team_ids = _list_ids(client, api, owner_h, library="team")
    assert shared_id in team_ids, "团队库不该因为我个人的封装而看不到这条素材"
    personal_ids = _list_ids(client, api, owner_h, library="personal")
    assert mine["id"] not in personal_ids


def test_pack_gets_the_pack_tag_automatically(client: TestClient, api: str) -> None:
    """类型筛选里没有「封装」这一档，找封装靠这个标签。

    标签是全局词表：两个封装共用同一条，不是每个封装各建一条同名的。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, name="tag-a.png", color="plum")
    second = upload_image(client, api, owner_h, name="tag-b.png", color="wheat")

    first_pack = _create_pack(
        client, api, owner_h, {"name": "带标签 1", "assetIds": [first["id"]]}
    ).json()["data"]
    second_pack = _create_pack(
        client, api, owner_h, {"name": "带标签 2", "assetIds": [second["id"]]}
    ).json()["data"]

    def pack_tag(pack: dict) -> str:
        names = [tag["id"] for tag in pack.get("tags") or [] if tag["name"] == "封装"]
        assert names, f"封装没有自动打上「封装」标签: {pack.get('tags')}"
        return names[0]

    assert pack_tag(first_pack) == pack_tag(second_pack)

    # 按标签能筛出这两条（类型筛选已经不含封装，标签是唯一入口）
    tag_id = pack_tag(first_pack)
    response = client.get(
        f"{api}/library/assets",
        params={"library": "personal", "tagIds": [tag_id], "pageSize": 100},
        headers=owner_h,
    )
    ids = {row["id"] for row in response.json()["data"]["items"]}
    assert {first_pack["id"], second_pack["id"]} <= ids


def test_selection_still_offers_packed_members(client: TestClient, api: str) -> None:
    """选择器要能挑到封装里的原素材。

    列表隐藏成员是为了「封装后只剩一条」，而选择器挑的恰恰就是那条要被使用的
    素材；把列表口径套过去，那 3 个原素材就会在生成页里彻底消失。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    keyword = "sel-member"
    member = upload_image(client, api, owner_h, name=f"{keyword}.png", color="silver")
    other = upload_image(client, api, owner_h, name="sel-packmate.png", color="indigo")
    _create_pack(
        client, api, owner_h, {"name": "选择器封装", "assetIds": [member["id"], other["id"]]}
    )

    hidden = _list_ids(client, api, owner_h)
    assert member["id"] not in hidden

    response = client.get(
        f"{api}/library/selection",
        params={"keyword": keyword, "includePacks": "false"},
        headers=owner_h,
    )
    ids = {row["id"] for row in response.json()["data"]["items"]}
    assert member["id"] in ids
