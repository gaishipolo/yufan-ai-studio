"""封装的建、列、取，以及「成员不再单列」。

四个断言方向：

1. **继承共同标签、不继承各自独有的**：`6036` 对整包有意义，`正面图/三视图`
   是成员各自的 —— 后者正是取图时区分成员的依据，打进包里会让整包看起来
   「什么角色都有」，取图就无从匹配。
2. **成员进了包就不再单列**：列表与角标同口径（否则翻页翻到空页、角标对不上）。
3. **包列表按槽位候选标签过滤**：这就是「产品包 / 服装包 / 人物包各自归位」
   的机制，不需要给包再加类型字段。
4. **成员被删仍然列出并标记**：包静默少一张，比露出来更糟。
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


def _pack(client: TestClient, headers: dict[str, str], name: str, ids: list[str]):
    return client.post(
        f"{API}/library/packs", json={"name": name, "assetIds": ids}, headers=headers
    )


def _asset_names(client: TestClient, headers: dict[str, str]) -> list[str]:
    body = client.get(f"{API}/library/assets", params={"pageSize": 100}, headers=headers)
    assert body.status_code == 200, body.text
    return [item["name"] for item in body.json()["data"]["items"]]


def test_pack_inherits_only_common_tags(client: TestClient) -> None:
    headers = _headers(client)
    product = _tag(client, headers, "6036")
    front = _tag(client, headers, "正面图")
    views = _tag(client, headers, "三视图")

    first = upload_image(client, API, headers, name="6036-黑-正面.png", color="red")
    second = upload_image(client, API, headers, name="6036-黑-三视图.png", color="blue")
    _tag_asset(client, headers, first["id"], [product["id"], front["id"]])
    _tag_asset(client, headers, second["id"], [product["id"], views["id"]])

    body = _pack(client, headers, "6036 黑", [first["id"], second["id"]])
    assert body.status_code == 201, body.text
    pack = body.json()["data"]
    assert pack["memberCount"] == 2
    # 并集：包的 tagIds 汇总了成员的标签，选择器靠它判断"能不能填这一格"
    assert set(pack["tagIds"]) == {product["id"], front["id"], views["id"]}

    detail = client.get(f"{API}/library/assets/{pack['id']}", headers=headers).json()["data"]
    assert detail["kind"] == "pack"
    # **包身上**只留共同标签：正面图/三视图不继承
    assert {item["name"] for item in detail["tags"]} == {"6036"}


def test_pack_rejects_unknown_or_nested_members(client: TestClient) -> None:
    headers = _headers(client)
    image = upload_image(client, API, headers, name="a.png", color="green")

    missing = _pack(client, headers, "空包", ["nonexistent-id"])
    assert missing.status_code == 400
    assert "不存在或无权访问" in missing.json()["message"]

    empty = client.post(
        f"{API}/library/packs", json={"name": "空包", "assetIds": []}, headers=headers
    )
    assert empty.status_code == 422

    pack = _pack(client, headers, "第一层", [image["id"]])
    assert pack.status_code == 201, pack.text
    nested = _pack(client, headers, "第二层", [pack.json()["data"]["id"]])
    assert nested.status_code == 400
    assert "不能嵌套" in nested.json()["message"]


def test_pack_members_stop_listing_separately(client: TestClient) -> None:
    headers = _headers(client)
    first = upload_image(client, API, headers, name="6036-黑-正面.png", color="yellow")
    second = upload_image(client, API, headers, name="6036-黑-三视图.png", color="cyan")

    before = _asset_names(client, headers)
    assert "6036-黑-正面.png" in before and "6036-黑-三视图.png" in before

    body = _pack(client, headers, "6036 黑", [first["id"], second["id"]])
    assert body.status_code == 201, body.text

    after = _asset_names(client, headers)
    # 成员不单列，包本身要出现
    assert "6036-黑-正面.png" not in after
    assert "6036-黑-三视图.png" not in after
    assert "6036 黑" in after


def test_pack_list_filters_by_slot_candidate_tags(client: TestClient) -> None:
    """槽位传自己的候选标签过来，只有含该标签成员的包才会出现。

    这就是「产品包 / 服装包 各自归位」的判据 —— 不给包加类型字段也能分工。
    """
    headers = _headers(client)
    front = _tag(client, headers, "正面图")
    dress = _tag(client, headers, "服装")

    with_front = upload_image(client, API, headers, name="f.png", color="orange")
    _tag_asset(client, headers, with_front["id"], [front["id"]])
    only_dress = upload_image(client, API, headers, name="d.png", color="white")
    _tag_asset(client, headers, only_dress["id"], [dress["id"]])

    assert _pack(client, headers, "产品包", [with_front["id"]]).status_code == 201
    assert _pack(client, headers, "服装包", [only_dress["id"]]).status_code == 201

    for_front = client.get(
        f"{API}/library/packs", params={"tagIds": [front["id"]]}, headers=headers
    ).json()["data"]
    assert [item["name"] for item in for_front] == ["产品包"]

    everything = client.get(f"{API}/library/packs", headers=headers).json()["data"]
    assert {item["name"] for item in everything} == {"产品包", "服装包"}


def test_pack_detail_marks_deleted_member(client: TestClient) -> None:
    """成员进了回收站仍然列出并标记 —— 包静默少一张，取图时才炸更糟。"""
    headers = _headers(client)
    keep = upload_image(client, API, headers, name="keep.png", color="purple")
    drop = upload_image(client, API, headers, name="drop.png", color="black")
    body = _pack(client, headers, "6036 黑", [keep["id"], drop["id"]])
    assert body.status_code == 201, body.text
    pack_id = body.json()["data"]["id"]

    removed = client.delete(f"{API}/library/assets/{drop['id']}", headers=headers)
    assert removed.status_code in (200, 204), removed.text

    detail = client.get(f"{API}/library/packs/{pack_id}", headers=headers).json()["data"]
    by_name = {item["name"]: item for item in detail["members"]}
    assert by_name["drop.png"]["deleted"] is True
    assert by_name["keep.png"]["deleted"] is False
    assert detail["memberCount"] == 2
