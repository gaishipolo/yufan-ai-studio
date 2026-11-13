"""「全部素材」入口的后端口径（设计 §8 / §12 第 20 条）。

前端把 `library=all` 做成默认落地页之后，这条不再是「选择器 / 分面」的内部
参数，而是用户每天看到的第一个列表，所以这里把三件事钉住：

1. `all` 是个人 / 小组 / 团队三个入口的**并集**，不多看别人的个人库，也不
   多看他组的共享；草稿与回收站不因为「全部」两个字混进来；
2. `facets.all` 等于 `library=all` 列表的 `total`（角标不能和点进去的条数打架）；
3. 标签栏在 `library=all` 下按同一套并集计数——共享出去之后个人入口少一条、
   全部入口仍算这一条。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    make_group,
    make_user,
    suffix,
    upload_image,
)

PERMISSIONS = LIB_RW + ["library:tag:manage"]
READ_ONLY = ["library:read"]


def _page(
    client: TestClient, api: str, headers: dict[str, str], **params: str
) -> dict:
    response = client.get(
        f"{api}/library/assets",
        params={"pageSize": 100, **params},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _ids(page: dict) -> list[str]:
    return [item["id"] for item in page["items"]]


def _share_to(
    client: TestClient,
    api: str,
    admin: dict[str, str],
    headers: dict[str, str],
    asset_id: str,
    *,
    target: str,
    group_id: str = "",
) -> None:
    created = client.post(
        f"{api}/library/share-requests",
        json={
            "assetId": asset_id,
            "action": "share",
            "target": target,
            "groupId": group_id,
            "reason": "",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["data"]["id"]
    approved = client.post(
        f"{api}/library/share-requests/{request_id}/approve", json={}, headers=admin
    )
    assert approved.status_code == 200, approved.text


def test_all_scope_is_the_union_of_personal_group_and_team(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, PERMISSIONS)
    viewer, viewer_h = make_user(client, admin, READ_ONLY)
    stranger, stranger_h = make_user(client, admin, PERMISSIONS)
    group = make_group(client, admin, [owner["id"], viewer["id"]])

    own = upload_image(client, api, owner_h, color="tomato")["id"]
    in_group = upload_image(client, api, owner_h, color="navy")["id"]
    in_team = upload_image(client, api, owner_h, color="olive")["id"]
    foreign = upload_image(client, api, stranger_h, color="teal")["id"]
    _share_to(client, api, admin, owner_h, in_group, target="group", group_id=group["id"])
    _share_to(client, api, admin, owner_h, in_team, target="team")

    everything = _page(client, api, owner_h, state="active", library="all")
    all_ids = _ids(everything)
    assert {own, in_group, in_team} <= set(all_ids)
    assert foreign not in all_ids
    # 角标与列表同一次查询算出，必须完全一致
    assert everything["facets"]["all"] == everything["total"]

    # 三个入口各自的条目都在并集里，且没有重复（同一素材只列一次）
    for tab, expected in (
        ("personal", own),
        ("group", in_group),
        ("team", in_team),
    ):
        # 只断言本用例造的那条：测试库是共享的，别的用例可能留下「跨入口可见
        # 的封装」，那种成员会出现在某个 tab 里却按全量口径藏起来（见
        # `access.hidden_by_pack_clause`），不是这里要测的东西。
        assert expected in _ids(_page(client, api, owner_h, state="active", library=tab))
    assert len(all_ids) == len(set(all_ids))

    # 组内成员：能看到小组/团队共享，看不到上传者的个人素材与组外人的个人素材
    viewer_ids = set(_ids(_page(client, api, viewer_h, state="active", library="all")))
    assert {in_group, in_team} <= viewer_ids
    assert own not in viewer_ids
    assert foreign not in viewer_ids

    # 组外成员：团队共享对全员可见，小组共享与他自己的个人素材各归其位
    stranger_ids = set(_ids(_page(client, api, stranger_h, state="active", library="all")))
    assert in_team in stranger_ids
    assert foreign in stranger_ids
    assert in_group not in stranger_ids
    assert own not in stranger_ids


def test_all_scope_excludes_trash_and_tag_counts_follow(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    _, headers = make_user(client, admin, PERMISSIONS)
    mark = suffix()

    tag = client.post(
        f"{api}/library/tags", json={"name": f"总览-{mark}"}, headers=headers
    )
    assert tag.status_code == 201, tag.text
    tag_id = tag.json()["data"]["id"]

    live = upload_image(client, api, headers, color="khaki")["id"]
    trashed = upload_image(client, api, headers, color="gray")["id"]
    for asset_id in (live, trashed):
        tagged = client.post(
            f"{api}/library/assets/bulk",
            json={"ids": [asset_id], "action": "addTag", "payload": {"tagIds": [tag_id]}},
            headers=headers,
        )
        assert tagged.status_code == 200, tagged.text
        assert tagged.json()["data"]["succeeded"] == 1

    deleted = client.delete(f"{api}/library/assets/{trashed}", headers=headers)
    assert deleted.status_code == 200, deleted.text

    def count(**params: str) -> int:
        response = client.get(f"{api}/library/tags", params=params, headers=headers)
        assert response.status_code == 200, response.text
        return next(
            item["assetCount"] for item in response.json()["data"] if item["id"] == tag_id
        )

    assert live in _ids(_page(client, api, headers, state="active", library="all"))
    assert trashed not in _ids(_page(client, api, headers, state="active", library="all"))
    assert trashed in _ids(_page(client, api, headers, state="trash"))
    assert count(state="active", library="all") == 1
    assert count(state="active", library="personal") == 1
    assert count(state="trash") == 1

    # 共享到团队后它离开个人入口，但「全部素材」的计数不变
    _share_to(client, api, admin, headers, live, target="team")
    assert count(state="active", library="all") == 1
    assert count(state="active", library="personal") == 0


def test_selection_takes_the_same_tab_as_the_list(client: TestClient, api: str) -> None:
    """选择器也要能按一级入口收窄（模板工作台的「我的 / 公共原片」）。

    这条钉的是**同一套判据**：`library` 走列表同一个枚举、同一个
    `access.tab_clause`，不是给选择器另写一份 `scope=mine|public`。
    旧前端不传这个参数时行为必须一个字不变（默认 `all`）。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, PERMISSIONS)
    viewer, viewer_h = make_user(client, admin, READ_ONLY)

    own = upload_image(client, api, owner_h, color="orchid")["id"]
    in_team = upload_image(client, api, owner_h, color="salmon")["id"]
    _share_to(client, api, admin, owner_h, in_team, target="team")

    def picked(headers: dict[str, str], **params: str) -> set[str]:
        response = client.get(
            f"{api}/library/selection",
            params={"pageSize": 100, **params},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["data"]["items"]}

    # 上传者：共享出去的那条离开个人入口，且不会同时出现在两个入口
    assert own in picked(owner_h, library="personal")
    assert in_team not in picked(owner_h, library="personal")
    assert in_team in picked(owner_h, library="team")
    assert own not in picked(owner_h, library="team")

    # 别人：团队共享就是「公共原片」的来源，个人入口里没有别人的东西
    assert in_team in picked(viewer_h, library="team")
    assert own not in picked(viewer_h, library="personal")

    # 不传 `library` = 全量可见集（与切换前的选择器完全一致）
    assert {own, in_team} <= picked(owner_h)
