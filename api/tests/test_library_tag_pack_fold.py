"""按标签筛选时，命中成员 → 返回**那个封装**；标签计数与列表同口径。

起因（2026-11-26 用户报）：资产库标签栏写「正面图 47」，点进去只有 7 条。

两件事一起才成立：

1. **列表**：成员进了封装就只在封装里出现（不单列），所以按成员独有的标签
   （如「正面图」）筛选时，该返回**包含它的那个封装**，而不是点不开的成员。
2. **计数**：成员折叠进它所属的封装，否则数字比点进去的条数大。

反过来，模板库按候选集统计时要**含成员**（那里挑的是成员，选择器不藏成员）：
传 `includePackMembers=true` 拿到的就是未折叠的数。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, upload_image

API = "/api/v1"


def _headers(client: TestClient) -> dict[str, str]:
    admin = admin_headers(client)
    _, headers = make_user(client, admin, LIB_RW)
    return headers


def _tag(client: TestClient, headers: dict[str, str], name: str) -> dict:
    response = client.post(f"{API}/library/tags", json={"name": name}, headers=headers)
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]


def _tag_asset(
    client: TestClient, headers: dict[str, str], asset_id: str, tag_ids: list[str]
) -> None:
    response = client.patch(
        f"{API}/library/assets/{asset_id}", json={"tagIds": tag_ids}, headers=headers
    )
    assert response.status_code == 200, response.text


def _tag_count(
    client: TestClient, headers: dict[str, str], tag_id: str, *, include_members: bool
) -> int:
    response = client.get(
        f"{API}/library/tags",
        params={"includePackMembers": str(include_members).lower()},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    for item in response.json()["data"]:
        if item["id"] == tag_id:
            return int(item["assetCount"])
    raise AssertionError(f"标签不在词表里：{tag_id}")


def _list_by_tag(
    client: TestClient, headers: dict[str, str], tag_id: str
) -> tuple[int, list[dict]]:
    response = client.get(
        f"{API}/library/assets",
        params={"tagIds": [tag_id], "pageSize": 100},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    return int(data["total"]), list(data["items"])


def test_按成员标签筛出的是封装不是成员(client: TestClient) -> None:
    """两个成员都带「正面图」→ 折叠后是**一个**封装，不是两条成员。"""
    headers = _headers(client)
    product = _tag(client, headers, "折叠用-产品")
    front = _tag(client, headers, "折叠用-正面图")

    first = upload_image(client, API, headers, name="折叠-正面-甲.png", color="red")
    second = upload_image(client, API, headers, name="折叠-正面-乙.png", color="blue")
    alone = upload_image(client, API, headers, name="折叠-独立正面.png", color="green")
    for asset in (first, second, alone):
        _tag_asset(client, headers, asset["id"], [product["id"]])
    _tag_asset(client, headers, first["id"], [product["id"], front["id"]])
    _tag_asset(client, headers, second["id"], [product["id"], front["id"]])
    _tag_asset(client, headers, alone["id"], [product["id"], front["id"]])

    pack = client.post(
        f"{API}/library/packs",
        json={"name": "折叠用-封装", "assetIds": [first["id"], second["id"]]},
        headers=headers,
    )
    assert pack.status_code in (200, 201), pack.text

    total, items = _list_by_tag(client, headers, front["id"])
    kinds = [item["kind"] for item in items]

    # 一个封装（两个成员折叠成一条）+ 一个独立素材 = 2 条；两个成员不单列。
    assert total == 2, items
    assert kinds.count("pack") == 1, items


def test_标签计数与列表条数一致(client: TestClient) -> None:
    """标签栏写 N、点进去就得是 N 条 —— 这是用户报的那个问题的本质。"""
    headers = _headers(client)
    product = _tag(client, headers, "口径用-产品")
    front = _tag(client, headers, "口径用-正面图")

    first = upload_image(client, API, headers, name="口径-正面-甲.png", color="red")
    second = upload_image(client, API, headers, name="口径-正面-乙.png", color="blue")
    alone = upload_image(client, API, headers, name="口径-独立正面.png", color="green")
    for asset in (first, second, alone):
        _tag_asset(client, headers, asset["id"], [product["id"], front["id"]])

    client.post(
        f"{API}/library/packs",
        json={"name": "口径用-封装", "assetIds": [first["id"], second["id"]]},
        headers=headers,
    )

    count = _tag_count(client, headers, front["id"], include_members=False)
    total, _ = _list_by_tag(client, headers, front["id"])
    assert count == total, f"标签栏写 {count}、点进去 {total} 条"


def test_候选集口径要能看见成员(client: TestClient) -> None:
    """模板库挑的是**成员**（选择器不藏成员）→ 计数不能折叠。"""
    headers = _headers(client)
    product = _tag(client, headers, "成员用-产品")
    front = _tag(client, headers, "成员用-正面图")

    first = upload_image(client, API, headers, name="成员-正面-甲.png", color="red")
    second = upload_image(client, API, headers, name="成员-正面-乙.png", color="blue")
    for asset in (first, second):
        _tag_asset(client, headers, asset["id"], [product["id"], front["id"]])

    client.post(
        f"{API}/library/packs",
        json={"name": "成员用-封装", "assetIds": [first["id"], second["id"]]},
        headers=headers,
    )

    folded = _tag_count(client, headers, front["id"], include_members=False)
    raw = _tag_count(client, headers, front["id"], include_members=True)
    assert raw > folded, f"含成员 {raw} 应该大于折叠后的 {folded}"
