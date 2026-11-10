"""封装自动继承成员标签。

四个断言方向：

1. 建封装时把**所有**成员的标签并集带过来，含「封装」固定标签与用户手选标签，去重；
2. 继承来的标签是真绑定，按成员标签能筛出封装（成员本身因封装而隐身）；
3. 换成员时只增不减：新成员的标签并进来，用户手动加 / 删的标签不被抹掉；
   卡片上的 ×（`PATCH /assets/{id}`）不会被自动补回，除非再次编辑成员；
4. `sync-pack-tags` 回填存量封装：试运行不写、写后能反复跑，第二次没有待补的。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, suffix, upload_image


def _tag(client: TestClient, api: str, headers: dict[str, str], name: str) -> dict:
    response = client.post(f"{api}/library/tags", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _add_tags(
    client: TestClient, api: str, headers: dict[str, str], asset_id: str, tag_ids: list[str]
) -> None:
    response = client.post(
        f"{api}/library/assets/bulk",
        json={"ids": [asset_id], "action": "addTag", "payload": {"tagIds": tag_ids}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["succeeded"] == 1


def _tags_of(client: TestClient, api: str, headers: dict[str, str], asset_id: str) -> list[dict]:
    response = client.get(f"{api}/library/assets/{asset_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]["tags"]


def _create_pack(
    client: TestClient, api: str, headers: dict[str, str], body: dict
) -> dict:
    response = client.post(f"{api}/library/packs", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_pack_inherits_every_member_tag(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    _, headers = make_user(client, admin, LIB_RW)
    mark = suffix()

    first = upload_image(client, api, headers, name=f"pk-a-{mark}.png", color="tomato")
    second = upload_image(client, api, headers, name=f"pk-b-{mark}.png", color="olive")
    tag_a = _tag(client, api, headers, f"人物-{mark}")
    tag_b = _tag(client, api, headers, f"产品-{mark}")
    tag_shared = _tag(client, api, headers, f"共用-{mark}")
    tag_manual = _tag(client, api, headers, f"手选-{mark}")
    _add_tags(client, api, headers, first["id"], [tag_a["id"], tag_shared["id"]])
    _add_tags(client, api, headers, second["id"], [tag_b["id"], tag_shared["id"]])

    pack = _create_pack(
        client,
        api,
        headers,
        {
            "name": f"标签并集-{mark}",
            "assetIds": [first["id"], second["id"]],
            "tagIds": [tag_manual["id"]],
        },
    )
    names = [tag["name"] for tag in pack["tags"]]
    assert set(names) == {
        "封装",
        f"人物-{mark}",
        f"产品-{mark}",
        f"共用-{mark}",
        f"手选-{mark}",
    }
    # 「共用」同时在两个成员上，不能在封装上出现两枚
    assert len(names) == len(set(names))

    # 按任一成员标签都该筛到封装；成员自己被 `hidden_by_pack_clause` 藏起来了
    for tag in (tag_a, tag_b, tag_shared):
        listed = client.get(
            f"{api}/library/assets",
            params={"library": "personal", "tagIds": [tag["id"]], "pageSize": 100},
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        ids = [item["id"] for item in listed.json()["data"]["items"]]
        assert pack["id"] in ids
        assert first["id"] not in ids and second["id"] not in ids


def test_member_replace_merges_new_tags_but_keeps_manual_edits(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    _, headers = make_user(client, admin, LIB_RW)
    mark = suffix()

    first = upload_image(client, api, headers, name=f"pk-c-{mark}.png", color="navy")
    second = upload_image(client, api, headers, name=f"pk-d-{mark}.png", color="teal")
    third = upload_image(client, api, headers, name=f"pk-e-{mark}.png", color="gray")
    tag_a = _tag(client, api, headers, f"老图-{mark}")
    tag_b = _tag(client, api, headers, f"新图-{mark}")
    tag_custom = _tag(client, api, headers, f"手动-{mark}")
    _add_tags(client, api, headers, first["id"], [tag_a["id"]])
    _add_tags(client, api, headers, third["id"], [tag_b["id"]])

    pack = _create_pack(
        client,
        api,
        headers,
        {"name": f"换成员-{mark}", "assetIds": [first["id"], second["id"]]},
    )

    # 卡片上的 ×：去掉继承来的标签，再手动加一个。普通 PATCH 不会被自动补回。
    current = [tag["id"] for tag in pack["tags"] if tag["id"] != tag_a["id"]]
    patched = client.patch(
        f"{api}/library/assets/{pack['id']}",
        json={"tagIds": [*current, tag_custom["id"]]},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    names = [tag["name"] for tag in _tags_of(client, api, headers, pack["id"])]
    assert f"老图-{mark}" not in names
    assert f"手动-{mark}" in names

    # 换成员：新成员的标签并进来，手动加的标签和「封装」都还在
    replaced = client.patch(
        f"{api}/library/packs/{pack['id']}",
        json={"items": [{"assetId": third["id"]}, {"assetId": second["id"]}]},
        headers=headers,
    )
    assert replaced.status_code == 200, replaced.text
    names = [tag["name"] for tag in replaced.json()["data"]["tags"]]
    assert f"新图-{mark}" in names
    assert f"手动-{mark}" in names
    assert "封装" in names
    # 老成员已经不在，刚被 × 去掉的标签也不会被补回：合并的是**当前成员**的
    # 标签并集，「只增不减」指不删封装上已有的绑定，不是把历史标签永久钉死。
    assert f"老图-{mark}" not in names


def test_sync_pack_tags_backfills_existing_packs(client: TestClient, api: str) -> None:
    from modules.library.maintenance import run_pack_tag_sync

    admin = admin_headers(client)
    owner, headers = make_user(client, admin, LIB_RW)
    mark = suffix()

    member = upload_image(client, api, headers, name=f"pk-f-{mark}.png", color="khaki")
    tag = _tag(client, api, headers, f"存量-{mark}")
    pack = _create_pack(
        client, api, headers, {"name": f"存量-{mark}", "assetIds": [member["id"]]}
    )
    # 模拟「封装建好之后，成员才被打标」：回填要把它补到封装上
    _add_tags(client, api, headers, member["id"], [tag["id"]])
    assert f"存量-{mark}" not in [item["name"] for item in pack["tags"]]

    scope = {"owner": owner["id"]}
    dry = asyncio.run(run_pack_tag_sync(dry_run=True, **scope))
    assert pack["id"] in dry.changed
    assert f"存量-{mark}" not in [
        item["name"] for item in _tags_of(client, api, headers, pack["id"])
    ]

    written = asyncio.run(run_pack_tag_sync(dry_run=False, **scope))
    assert pack["id"] in written.changed
    assert f"存量-{mark}" in [item["name"] for item in _tags_of(client, api, headers, pack["id"])]

    again = asyncio.run(run_pack_tag_sync(dry_run=True, **scope))
    assert pack["id"] not in again.changed, "回填必须幂等，第二次不该再有差"
