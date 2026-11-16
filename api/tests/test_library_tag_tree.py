"""标签两级词表：层级约束、计数归并、筛选展开、打标守卫、删除提升。

一级是分组、二级才是打在素材上的词；但有子标签的标签一律不可打标（孤立一级
仍是叶子，别把存量词表搞成死词）。三条口径必须一致，否则「标签旁写 8、
点进去只有 3 条」会立刻复现：

- 计数：一级 = 自身 + 旗下二级，按资产去重；
- 筛选：选中一级展开成「自身 + 直属子级」；
- 打标：有子标签的分组标签拒收。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, suffix, upload_image

PERMS = [*LIB_RW, "library:tag:manage"]


def _create(
    client: TestClient,
    api: str,
    headers: dict[str, str],
    name: str,
    parent: str | None = None,
) -> dict:
    body: dict = {"name": name}
    if parent:
        body["parentId"] = parent
    response = client.post(f"{api}/library/tags", json=body, headers=headers)
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
    assert response.json()["data"]["succeeded"] == 1, response.text


def _tags(client: TestClient, api: str, headers: dict[str, str]) -> dict[str, dict]:
    response = client.get(f"{api}/library/tags", headers=headers)
    assert response.status_code == 200, response.text
    return {item["id"]: item for item in response.json()["data"]}


def _filter_total(
    client: TestClient, api: str, headers: dict[str, str], tag_ids: list[str]
) -> int:
    response = client.get(
        f"{api}/library/assets",
        params=[("tagIds", tag_id) for tag_id in tag_ids],
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return int(response.json()["data"]["total"])


def _setup(client: TestClient, api: str) -> tuple[dict[str, str], dict[str, str]]:
    admin = admin_headers(client)
    _, headers = make_user(client, admin, PERMS)
    return admin, headers


def test_tags_expose_hierarchy_as_a_flat_list(client: TestClient, api: str) -> None:
    _, headers = _setup(client, api)
    mark = suffix()
    parent = _create(client, api, headers, f"产品-{mark}")
    child = _create(client, api, headers, f"3030-{mark}", parent["id"])

    listed = _tags(client, api, headers)
    assert listed[parent["id"]]["depth"] == 1
    assert listed[parent["id"]]["parentId"] is None
    assert listed[parent["id"]]["childCount"] == 1
    assert listed[child["id"]]["depth"] == 2
    assert listed[child["id"]]["parentId"] == parent["id"]
    assert listed[child["id"]]["parentName"] == f"产品-{mark}"
    assert listed[child["id"]]["childCount"] == 0
    # 仍是平铺数组，不是嵌套树：六个消费点都按平铺渲染
    assert parent["id"] in listed and child["id"] in listed


def test_parent_count_is_deduped_union_and_filter_expands(client: TestClient, api: str) -> None:
    _, headers = _setup(client, api)
    mark = suffix()
    parent = _create(client, api, headers, f"产品-{mark}")
    a = upload_image(client, api, headers, name=f"tree-a-{mark}.png", color="tomato")
    c = upload_image(client, api, headers, name=f"tree-c-{mark}.png", color="navy")
    # 先挂一级（此时它还没有子标签，是叶子）：模拟两级改造前的存量绑定
    _add_tags(client, api, headers, a["id"], [parent["id"]])
    _add_tags(client, api, headers, c["id"], [parent["id"]])

    child = _create(client, api, headers, f"3030-{mark}", parent["id"])
    b = upload_image(client, api, headers, name=f"tree-b-{mark}.png", color="olive")
    d = upload_image(client, api, headers, name=f"tree-d-{mark}.png", color="teal")
    _add_tags(client, api, headers, b["id"], [child["id"]])
    _add_tags(client, api, headers, c["id"], [child["id"]])  # 父子同挂一张图
    _add_tags(client, api, headers, d["id"], [child["id"]])

    listed = _tags(client, api, headers)
    # 一级 = 自身 {a,c} ∪ 子级 {b,c,d}，按资产去重 = 4（c 不能算两次）
    assert listed[parent["id"]]["assetCount"] == 4
    assert listed[child["id"]]["assetCount"] == 3
    assert _filter_total(client, api, headers, [parent["id"]]) == 4
    assert _filter_total(client, api, headers, [child["id"]]) == 3
    # 多选仍是 OR，且同一张图不重复
    assert _filter_total(client, api, headers, [parent["id"], child["id"]]) == 4


def test_three_level_multi_select_is_a_deduped_union(client: TestClient, api: str) -> None:
    """三级标签任意组合：跨链多选是 OR，同一张图只算一次。

    三处口径必须一致，否则「标签旁写 8、点进去只有 3 条」会立刻复现：

    - 计数：一个标签 = 自身 + 全部下级，按**资产 id** 去重；
    - 筛选：选中一级要带上它的二级与三级，选中二级要带上三级；
    - 总数：`total` 与 `items` 同源，所以一条图挂了父子两个标签也只出现一次。

    跨链多选（产品 + 角色）是并集；同一链上父子同时选中时，后端仍然是并集
    （父的范围本来就含子），**界面**在发请求前会把同链的选择收敛成最具体的那
    一层（见前端 `effectiveTagSelection`）—— 后端这一层只保证「不重复计数」。
    """
    _, headers = _setup(client, api)
    mark = suffix()
    product = _create(client, api, headers, f"产品-{mark}")
    role = _create(client, api, headers, f"角色-{mark}")

    a = upload_image(client, api, headers, name=f"multi-a-{mark}.png", color="tomato")
    b = upload_image(client, api, headers, name=f"multi-b-{mark}.png", color="navy")
    c = upload_image(client, api, headers, name=f"multi-c-{mark}.png", color="olive")
    d = upload_image(client, api, headers, name=f"multi-d-{mark}.png", color="teal")
    # 只能给**叶子**打标（分组标签不能直接打在素材上），所以顺序是：
    # 先在一级上打标 → 建二级 → 在二级上打标 → 建三级 → 在三级上打标。
    # 这样三条素材分别落在一级 / 二级 / 三级上，三个层级的「自身 + 全部下级」
    # 才有不同的数。
    _add_tags(client, api, headers, a["id"], [product["id"]])
    _add_tags(client, api, headers, d["id"], [role["id"]])
    series = _create(client, api, headers, f"3030-{mark}", product["id"])
    _add_tags(client, api, headers, b["id"], [series["id"]])
    leaf = _create(client, api, headers, f"T35-{mark}", series["id"])
    _add_tags(client, api, headers, c["id"], [leaf["id"]])

    listed = _tags(client, api, headers)
    # 计数 = 自身 + 全部下级，按资产去重
    assert listed[product["id"]]["assetCount"] == 3
    assert listed[series["id"]]["assetCount"] == 2
    assert listed[leaf["id"]]["assetCount"] == 1
    assert listed[role["id"]]["assetCount"] == 1

    # 筛选：选哪一级都带上它的全部下级
    assert _filter_total(client, api, headers, [product["id"]]) == 3
    assert _filter_total(client, api, headers, [series["id"]]) == 2
    assert _filter_total(client, api, headers, [leaf["id"]]) == 1

    # 跨链多选 = 并集
    assert _filter_total(client, api, headers, [product["id"], role["id"]]) == 4
    assert _filter_total(client, api, headers, [series["id"], role["id"]]) == 3
    # 同一链上父子同时选中：父的范围含子，结果仍是父的范围（不重复计数）
    assert _filter_total(client, api, headers, [product["id"], series["id"]]) == 3
    assert _filter_total(client, api, headers, [series["id"], leaf["id"]]) == 2

    # 三级 + 跨链一起选：并集，且一张图不会因为挂了两个标签而算两次
    assert _filter_total(client, api, headers, [leaf["id"], role["id"]]) == 2

    # 总数与列表条数同源：翻页不会出现「总数 4、这一页只有 3 条」
    response = client.get(
        f"{api}/library/assets",
        params=[("tagIds", product["id"]), ("tagIds", role["id"]), ("pageSize", 2)],
        headers=headers,
    )
    assert response.status_code == 200, response.text
    page = response.json()["data"]
    assert page["total"] == 4
    assert len(page["items"]) == 2


def test_group_tags_cannot_be_assigned_but_leaf_tags_can(client: TestClient, api: str) -> None:
    _, headers = _setup(client, api)
    mark = suffix()
    group = _create(client, api, headers, f"产品-{mark}")
    _create(client, api, headers, f"3030-{mark}", group["id"])
    leaf = _create(client, api, headers, f"音乐-{mark}")
    child = next(
        item
        for item in _tags(client, api, headers).values()
        if item["parentId"] == group["id"]
    )
    asset = upload_image(client, api, headers, name=f"tree-leaf-{mark}.png", color="purple")

    rejected = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"tagIds": [group["id"]]},
        headers=headers,
    )
    assert rejected.status_code == 400, rejected.text
    assert "一级标签只做分组" in rejected.json()["message"]

    ok = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"tagIds": [child["id"], leaf["id"]]},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    assert {item["id"] for item in ok.json()["data"]["tags"]} == {child["id"], leaf["id"]}
    assert {item["parentName"] for item in ok.json()["data"]["tags"] if item["parentName"]} == {
        f"产品-{mark}"
    }


def test_create_allows_three_levels_and_rejects_a_fourth(
    client: TestClient, api: str
) -> None:
    """三级是上限：第三级能建，第四级拒绝。

    层数封在三级不是随手定的 —— 标签栏是三行选择器，再深就得改成树控件，
    而筛选与计数的口径也要多套一层。
    """
    _, headers = _setup(client, api)
    mark = suffix()
    parent = _create(client, api, headers, f"产品-{mark}")
    child = _create(client, api, headers, f"3030-{mark}", parent["id"])

    third = client.post(
        f"{api}/library/tags",
        json={"name": f"T35-{mark}", "parentId": child["id"]},
        headers=headers,
    )
    assert third.status_code == 201, third.text
    assert third.json()["data"]["depth"] == 3
    assert third.json()["data"]["parentName"] == f"产品-{mark} / 3030-{mark}"

    response = client.post(
        f"{api}/library/tags",
        json={"name": f"第四级-{mark}", "parentId": third.json()["data"]["id"]},
        headers=headers,
    )
    assert response.status_code == 400, response.text


def test_move_guards_and_promotion(client: TestClient, api: str) -> None:
    _, headers = _setup(client, api)
    mark = suffix()
    a = _create(client, api, headers, f"甲-{mark}")
    b = _create(client, api, headers, f"乙-{mark}")
    a1 = _create(client, api, headers, f"甲子-{mark}", a["id"])

    def move(tag_id: str, parent: str | None) -> object:
        return client.patch(
            f"{api}/library/tags/{tag_id}/parent",
            json={"parentId": parent},
            headers=headers,
        )

    assert move(a1["id"], a1["id"]).status_code == 400  # 不能挂自己
    assert move(a["id"], a1["id"]).status_code == 400  # 不能挂到自己的下级下面
    # 挂到二级下面是可以的（三级树的常态），但整棵子树不能超出三层：
    # 乙下面挂甲，甲下面挂甲子 —— 三层刚好，再多一层就超了。
    # 子树高度会让层数超限 → 拒：甲(1) 甲子(2) 甲甲甲(3)，
    # 把甲子挂到二级「乙子」下面，整棵子树就变成 2+2=4 层了。
    _create(client, api, headers, f"甲甲甲-{mark}", a1["id"])
    b1 = _create(client, api, headers, f"乙子-{mark}", b["id"])
    assert move(a1["id"], b1["id"]).status_code == 400  # 整棵子树会超过三层

    moved = move(a1["id"], b["id"])
    assert moved.status_code == 200, moved.text
    assert moved.json()["data"]["parentId"] == b["id"]
    assert moved.json()["data"]["parentName"] == f"乙-{mark}"

    promoted = move(a1["id"], None)
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["data"]["parentId"] is None
    assert promoted.json()["data"]["depth"] == 1


def test_name_uniqueness_is_scoped_to_the_same_parent(client: TestClient, api: str) -> None:
    _, headers = _setup(client, api)
    mark = suffix()
    a = _create(client, api, headers, f"甲-{mark}")
    b = _create(client, api, headers, f"乙-{mark}")
    same = f"小红书-{mark}"
    a1 = _create(client, api, headers, same, a["id"])
    b1 = _create(client, api, headers, same, b["id"])
    assert a1["id"] != b1["id"]  # 不同一级下允许同名二级

    conflict = client.patch(
        f"{api}/library/tags/{b1['id']}/parent",
        json={"parentId": a["id"]},
        headers=headers,
    )
    assert conflict.status_code == 409, conflict.text

    renamed = client.patch(
        f"{api}/library/tags/{b1['id']}", json={"name": f"改-{mark}"}, headers=headers
    )
    assert renamed.status_code == 200, renamed.text
    clash = client.patch(
        f"{api}/library/tags/{b1['id']}/parent",
        json={"parentId": b["id"]},
        headers=headers,
    )
    assert clash.status_code == 200, clash.text


def test_delete_promotes_children_and_keeps_asset_bindings(client: TestClient, api: str) -> None:
    _, headers = _setup(client, api)
    mark = suffix()
    parent = _create(client, api, headers, f"产品-{mark}")
    child = _create(client, api, headers, f"3030-{mark}", parent["id"])
    asset = upload_image(client, api, headers, name=f"tree-del-{mark}.png", color="salmon")
    _add_tags(client, api, headers, asset["id"], [child["id"]])

    response = client.delete(f"{api}/library/tags/{parent['id']}", headers=headers)
    assert response.status_code == 200, response.text
    listed = _tags(client, api, headers)
    assert parent["id"] not in listed
    assert listed[child["id"]]["depth"] == 1
    assert listed[child["id"]]["parentId"] is None
    assert _filter_total(client, api, headers, [child["id"]]) >= 1


def test_delete_refuses_promotion_that_would_clash(client: TestClient, api: str) -> None:
    _, headers = _setup(client, api)
    mark = suffix()
    same = f"同名-{mark}"
    parent = _create(client, api, headers, f"产品-{mark}")
    _create(client, api, headers, same, parent["id"])
    _create(client, api, headers, same)  # 另一个一级同名，二级与它不同父所以合法

    response = client.delete(f"{api}/library/tags/{parent['id']}", headers=headers)
    assert response.status_code == 409, response.text
    assert "同名" in response.json()["message"]
    # 冲突时不能先把子级改了再删：整轮应该原样保留
    assert parent["id"] in _tags(client, api, headers)
