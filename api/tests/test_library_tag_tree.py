"""标签两级词表：层级约束、计数归并、筛选语义、打标守卫、删除提升。

一级是**维度**、二级是**取值**；有子标签的一级不可打标（孤立一级仍是叶子，
别把存量词表搞成死词）。三条口径必须一致，否则「标签旁写 8、点进去只有 3 条」
会立刻复现：

- 计数：一级 = 自身 + 旗下二级，按资产去重；
- 筛选：**同维度多选是或、跨维度是且**（维度模型的核心语义）；
- 打标：有子标签的维度标签拒收。
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
    # 同一维度里多选是**或**：父的范围含子，两支都要看到，且同一张图不重复
    assert _filter_total(client, api, headers, [parent["id"], child["id"]]) == 4


def test_multi_select_is_or_within_a_dimension_and_and_across_dimensions(
    client: TestClient, api: str
) -> None:
    """两级维度模型的筛选语义：**同维度多选是或，跨维度是且**。

    这是本次改造的核心。旧的三级模型是「全 OR」—— 因为那时三条链是并列的
    分类轴。两级之后「画面类型」与「产品」是**互相独立**的两条轴，
    并集会把「6036 的三视图」也带出来，而用户要的是交集。

    三处口径必须一致，否则「标签旁写 8、点进去只有 3 条」会立刻复现：

    - 计数：一个标签 = 自身 + 全部下级，按**资产 id** 去重；
    - 筛选：选中一级要带上它的全部二级；同维度内多选取并集，跨维度取交集；
    - 总数：`total` 与 `items` 同源，所以一条图挂了父子两个标签也只出现一次。
    """
    _, headers = _setup(client, api)
    mark = suffix()
    product = _create(client, api, headers, f"产品-{mark}")
    shape = _create(client, api, headers, f"画面类型-{mark}")
    role = _create(client, api, headers, f"角色-{mark}")

    p6036 = _create(client, api, headers, f"6036-{mark}", product["id"])
    pT35 = _create(client, api, headers, f"T35-{mark}", product["id"])
    front = _create(client, api, headers, f"正面图-{mark}", shape["id"])
    side = _create(client, api, headers, f"侧面图-{mark}", shape["id"])

    a = upload_image(client, api, headers, name=f"dim-a-{mark}.png", color="tomato")
    b = upload_image(client, api, headers, name=f"dim-b-{mark}.png", color="navy")
    c = upload_image(client, api, headers, name=f"dim-c-{mark}.png", color="olive")
    d = upload_image(client, api, headers, name=f"dim-d-{mark}.png", color="teal")
    # 6036 的正面图：同时在「产品」与「画面类型」两个维度上
    _add_tags(client, api, headers, a["id"], [p6036["id"], front["id"]])
    # 6036 的侧面图
    _add_tags(client, api, headers, b["id"], [p6036["id"], side["id"]])
    # T35 的正面图
    _add_tags(client, api, headers, c["id"], [pT35["id"], front["id"]])
    # 只有角色维度
    _add_tags(client, api, headers, d["id"], [role["id"]])

    listed = _tags(client, api, headers)
    # 计数 = 自身 + 全部下级，按资产去重
    assert listed[product["id"]]["assetCount"] == 3
    assert listed[p6036["id"]]["assetCount"] == 2
    assert listed[shape["id"]]["assetCount"] == 3
    assert listed[front["id"]]["assetCount"] == 2
    assert listed[role["id"]]["assetCount"] == 1

    # 单选：选中一级带上它的全部取值
    assert _filter_total(client, api, headers, [product["id"]]) == 3
    assert _filter_total(client, api, headers, [p6036["id"]]) == 2
    assert _filter_total(client, api, headers, [front["id"]]) == 2

    # **同维度多选 = 或**：两个产品都要看到
    assert _filter_total(client, api, headers, [p6036["id"], pT35["id"]]) == 3
    # 跨维度多选 = 且：既是 6036 又是正面图 → 只有 a
    assert _filter_total(client, api, headers, [p6036["id"], front["id"]]) == 1
    # 跨维度多选 = 且：既是任意产品又是正面图 → a 与 c（不是 b）
    assert _filter_total(client, api, headers, [product["id"], front["id"]]) == 2
    # 跨维度多选 = 且：既是任意产品又是侧面图 → 只有 b
    assert _filter_total(client, api, headers, [product["id"], side["id"]]) == 1
    # 三个维度一起选：每个维度各命中至少一条 → 空集
    assert (
        _filter_total(client, api, headers, [p6036["id"], front["id"], role["id"]]) == 0
    )

    # 同一维度里选了维度又选了取值：**同维度是或**，维度的范围含取值
    assert _filter_total(client, api, headers, [product["id"], p6036["id"]]) == 3

    # 总数与列表条数同源：翻页不会出现「总数 4、这一页只有 3 条」
    response = client.get(
        f"{api}/library/assets",
        params=[("tagIds", p6036["id"]), ("tagIds", pT35["id"]), ("pageSize", 2)],
        headers=headers,
    )
    assert response.status_code == 200, response.text
    page = response.json()["data"]
    assert page["total"] == 3
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
    assert "一级标签是分组（维度）" in rejected.json()["message"]

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


def test_create_allows_two_levels_and_rejects_a_third(
    client: TestClient, api: str
) -> None:
    """两级是上限：第二级能建，第三级拒绝。

    层数封在两级不是随手定的 —— 两级刚好是「维度 / 取值」的最小表达：
    一条轴自己是一棵两级小树（产品 → 6036），轴之间彼此正交
    （画面类型 → 正面图）。再深就得把跨轴复用的词塞进某个具体实例的深处，
    每新增一个产品就要把整套词复制一遍。
    """
    _, headers = _setup(client, api)
    mark = suffix()
    parent = _create(client, api, headers, f"产品-{mark}")
    child = _create(client, api, headers, f"3030-{mark}", parent["id"])
    assert child["depth"] == 2

    third = client.post(
        f"{api}/library/tags",
        json={"name": f"T35-{mark}", "parentId": child["id"]},
        headers=headers,
    )
    assert third.status_code == 400, third.text
    assert "标签最多 2 级" in third.json()["message"]


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
    # 挂到取值下面会造出第三层 → 拒（取值只能挂在维度下面）
    assert move(a1["id"], a1["id"]).status_code == 400

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


def test_tag_counts_can_be_narrowed_to_a_candidate_scope(
    client: TestClient, api: str
) -> None:
    """`GET /library/tags?tagIds=…` 只统计符合候选范围的那批素材。

    模板库要显示的是「这批素材身上实际有的二级标签」——按全库算会列出一堆
    这批素材一个都没挂的标签，用户点了只得到空列表。
    """
    _, headers = _setup(client, api)
    mark = suffix()
    product = _create(client, api, headers, f"产品-{mark}")
    view = _create(client, api, headers, f"画面类型-{mark}")
    model_6036 = _create(client, api, headers, f"6036-{mark}", product["id"])
    model_t40 = _create(client, api, headers, f"T40-{mark}", product["id"])
    front = _create(client, api, headers, f"正面图-{mark}", view["id"])

    inside = upload_image(client, api, headers, name=f"in-{mark}.png", color="salmon")
    _add_tags(client, api, headers, inside["id"], [model_6036["id"], front["id"]])
    outside = upload_image(client, api, headers, name=f"out-{mark}.png", color="teal")
    _add_tags(client, api, headers, outside["id"], [model_t40["id"]])

    # 不收窄：两个产品取值都有素材
    full = _tags(client, api, headers)
    assert full[model_6036["id"]]["assetCount"] >= 1
    assert full[model_t40["id"]]["assetCount"] >= 1

    # 收窄到「6036」：T40 那条素材不在范围里，它的计数归零
    scoped = client.get(
        f"{api}/library/tags",
        params=[("tagIds", model_6036["id"])],
        headers=headers,
    )
    assert scoped.status_code == 200, scoped.text
    counts = {item["id"]: item["assetCount"] for item in scoped.json()["data"]}
    assert counts[model_6036["id"]] == 1
    assert counts[model_t40["id"]] == 0
    # 范围内素材身上的另一个标签仍然算数
    assert counts[front["id"]] == 1

    # 跨维度收窄（组内 OR、组间 AND）：范围是「6036 且 正面图」
    both = client.get(
        f"{api}/library/tags",
        params=[("tagIds", model_6036["id"]), ("tagIds", front["id"])],
        headers=headers,
    )
    assert both.status_code == 200, both.text
    counts = {item["id"]: item["assetCount"] for item in both.json()["data"]}
    assert counts[model_6036["id"]] == 1
    assert counts[front["id"]] == 1


def test_scoped_tag_counts_match_the_asset_list(
    client: TestClient, api: str
) -> None:
    """收窄后的计数必须与「按同一组标签筛素材」的结果对得上。

    对不上就是「标签旁写 8、点进去只有 3 条」——那是标签栏最招人烦的一种错。
    """
    _, headers = _setup(client, api)
    mark = suffix()
    product = _create(client, api, headers, f"产品-{mark}")
    model_a = _create(client, api, headers, f"A-{mark}", product["id"])
    model_b = _create(client, api, headers, f"B-{mark}", product["id"])

    first = upload_image(client, api, headers, name=f"a1-{mark}.png", color="salmon")
    _add_tags(client, api, headers, first["id"], [model_a["id"]])
    second = upload_image(client, api, headers, name=f"a2-{mark}.png", color="teal")
    _add_tags(client, api, headers, second["id"], [model_b["id"]])

    scoped = client.get(
        f"{api}/library/tags",
        params=[("tagIds", model_a["id"])],
        headers=headers,
    )
    assert scoped.status_code == 200, scoped.text
    counts = {item["id"]: item["assetCount"] for item in scoped.json()["data"]}
    listed = _filter_total(client, api, headers, [model_a["id"]])
    assert counts[model_a["id"]] == listed
