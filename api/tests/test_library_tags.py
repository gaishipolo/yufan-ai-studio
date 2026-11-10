"""标签栏：计数、创建时间排序、入口隔离。

标签栏上的「标签 · N」必须与点进去之后看到的条数对得上，所以计数要走
`LibraryService.list_assets` 同一套可见条件，而不是简单数 `library_asset_tags`
里的行数。这里三个断言方向：

1. 计数只看当前入口（个人 / 草稿 / 回收站）且只看自己看得见的资产；
2. 返回全部标签（含 0 条的），按添加时间升序，带 `createdAt`；
3. 去标签（PATCH `tagIds`）与删除资产之后，计数立刻跟着变。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    make_user,
    suffix,
    upload_image,
)


def _create_tag(client: TestClient, api: str, headers: dict[str, str], name: str) -> dict:
    response = client.post(f"{api}/library/tags", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _add_tags(
    client: TestClient,
    api: str,
    headers: dict[str, str],
    asset_id: str,
    tag_ids: list[str],
) -> None:
    response = client.post(
        f"{api}/library/assets/bulk",
        json={"ids": [asset_id], "action": "addTag", "payload": {"tagIds": tag_ids}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["succeeded"] == 1


def _counts(
    client: TestClient,
    api: str,
    headers: dict[str, str],
    *,
    state: str = "active",
    library: str = "personal",
) -> dict[str, dict]:
    response = client.get(
        f"{api}/library/tags", params={"state": state, "library": library}, headers=headers
    )
    assert response.status_code == 200, response.text
    return {item["id"]: item for item in response.json()["data"]}


def test_tag_counts_follow_the_current_scope(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    _, other_h = make_user(client, admin, LIB_RW)
    mark = suffix()

    first = upload_image(client, api, owner_h, name=f"tag-a-{mark}.png", color="tomato")
    second = upload_image(client, api, owner_h, name=f"tag-b-{mark}.png", color="navy")
    other = upload_image(client, api, other_h, name=f"tag-c-{mark}.png", color="olive")

    tag_a = _create_tag(client, api, owner_h, f"甲-{mark}")
    tag_b = _create_tag(client, api, owner_h, f"乙-{mark}")
    tag_c = _create_tag(client, api, owner_h, f"空-{mark}")
    _add_tags(client, api, owner_h, first["id"], [tag_a["id"], tag_b["id"]])
    _add_tags(client, api, owner_h, second["id"], [tag_a["id"]])
    _add_tags(client, api, other_h, other["id"], [tag_a["id"]])

    listed = _counts(client, api, owner_h)
    # 别人的个人库素材不能算进我的计数里
    assert listed[tag_a["id"]]["assetCount"] == 2
    assert listed[tag_b["id"]]["assetCount"] == 1
    # 0 条的标签也要出现在标签栏里，否则新建完找不到它
    assert listed[tag_c["id"]]["assetCount"] == 0
    assert all("createdAt" in item for item in listed.values())


def test_tags_are_ordered_by_creation_time(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    _, headers = make_user(client, admin, LIB_RW)
    mark = suffix()
    for index in range(4):
        _create_tag(client, api, headers, f"排序-{mark}-{index}")

    response = client.get(f"{api}/library/tags", headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()["data"]
    created = [item["createdAt"] for item in items]
    assert created == sorted(created)
    assert len({item["id"] for item in items}) == len(items)


def test_tag_counts_update_after_untag_and_trash(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    mark = suffix()

    asset = upload_image(client, api, owner_h, name=f"tag-untag-{mark}.png", color="teal")
    tag_a = _create_tag(client, api, owner_h, f"去标-{mark}")
    tag_b = _create_tag(client, api, owner_h, f"保留-{mark}")
    _add_tags(client, api, owner_h, asset["id"], [tag_a["id"], tag_b["id"]])
    assert _counts(client, api, owner_h)[tag_a["id"]]["assetCount"] == 1

    # 卡片上的 ×：整体替换成「只留 tag_b」，计数与详情都要跟着变
    patched = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"tagIds": [tag_b["id"]]},
        headers=owner_h,
    )
    assert patched.status_code == 200, patched.text
    assert [item["name"] for item in patched.json()["data"]["tags"]] == [f"保留-{mark}"]
    after = _counts(client, api, owner_h)
    assert after[tag_a["id"]]["assetCount"] == 0
    assert after[tag_b["id"]]["assetCount"] == 1

    # 移入回收站：个人入口不再计，回收站入口仍计（标签绑定没被清掉）
    deleted = client.delete(f"{api}/library/assets/{asset['id']}", headers=owner_h)
    assert deleted.status_code == 200, deleted.text
    assert _counts(client, api, owner_h)[tag_b["id"]]["assetCount"] == 0
    assert _counts(client, api, owner_h, state="trash")[tag_b["id"]]["assetCount"] == 1
