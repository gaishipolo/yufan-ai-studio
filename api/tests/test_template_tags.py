"""模板独立标签词表：两级 CRUD / 打标 / 筛选。

走 HTTP（不是直调 service）：要验的正是「路由 + 契约序列化 + service」整条链路。
两级口径与素材库一致（同维度 OR、跨维度 AND），但词表独立 —— 守卫里要确认
模板打标不会污染素材库词表。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, suffix

API = "/api/v1/template-specs"
PERMS = [
    "template_pack:read",
    "template_pack:create",
    "template_pack:publish",
    "catalog:read",
    *LIB_RW,
]
MODEL = "libtv-wan3-replacement"


def _headers(client: TestClient) -> dict[str, str]:
    _, headers = make_user(client, admin_headers(client), PERMS)
    return headers


def _tag(client: TestClient, headers: dict[str, str], name: str, parent: str | None = None) -> str:
    response = client.post(
        f"{API}/tags",
        json={"name": f"{name}-{suffix()}", "parentId": parent},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _spec(client: TestClient, headers: dict[str, str], name: str) -> str:
    """建一条最小草稿（不需要素材/槽位，标签筛选只看 head 的 tag_ids）。"""
    payload: dict[str, Any] = {
        "key": f"tagspec-{suffix()}",
        "name": name,
        "model": MODEL,
        "prompt": "一句话提示词",
        "slots": [],
    }
    response = client.post(API, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]["key"]


def test_两级词表与打标走完整链路(client: TestClient) -> None:
    headers = _headers(client)
    dimension = _tag(client, headers, "用途")
    value_a = _tag(client, headers, "产品图", dimension)
    value_b = _tag(client, headers, "场景图", dimension)

    tags = client.get(f"{API}/tags", headers=headers).json()["data"]
    by_id = {item["id"]: item for item in tags}
    assert by_id[dimension]["depth"] == 1
    assert by_id[value_a]["depth"] == 2
    assert by_id[value_a]["parentName"].startswith("用途")

    key = _spec(client, headers, "打标测试")
    response = client.patch(
        f"{API}/{key}/tags", json={"tagIds": [value_a, value_b]}, headers=headers
    )
    assert response.status_code == 200, response.text
    # 打标不改草稿：revision 不涨
    assert response.json()["data"]["revision"] == 1
    again = client.get(f"{API}/{key}", headers=headers).json()["data"]
    assert set(again["tagIds"]) == {value_a, value_b}

    # 计数：一级维度要归拢它下面所有取值的模板（打的是取值，维度本身不能打）。
    # 少这一步的表现是「实拍模板 0，点进去却有 1 条」。
    counts = {
        item["id"]: item["specCount"]
        for item in client.get(f"{API}/tags", headers=headers).json()["data"]
    }
    assert counts[value_a] == 1
    assert counts[value_b] == 1
    assert counts[dimension] == 1  # 两个取值挂在同一个模板上，按模板去重仍为 1


def test_标签计数按入口收窄(client: TestClient) -> None:
    """模板封装看「我的」、模板库看「已发布」，计数不能是全库口径。"""
    owner = _headers(client)
    other = _headers(client)
    dimension = _tag(client, owner, "计数口径")
    value = _tag(client, owner, "计数取值", dimension)

    mine = _spec(client, owner, "我的模板")
    theirs = _spec(client, other, "别人的模板")
    for key, headers in ((mine, owner), (theirs, other)):
        response = client.patch(
            f"{API}/{key}/tags", json={"tagIds": [value]}, headers=headers
        )
        assert response.status_code == 200, response.text

    def count(headers: dict[str, str], scope: str) -> int:
        rows = client.get(f"{API}/tags", params={"scope": scope}, headers=headers).json()["data"]
        return next(item["specCount"] for item in rows if item["id"] == value)

    assert count(owner, "all") == 2
    assert count(owner, "mine") == 1
    assert count(other, "mine") == 1

    published = client.post(f"{API}/{mine}/publish", headers=owner)
    assert published.status_code == 200, published.text
    assert count(owner, "published") == 1
    assert count(other, "published") == 1


def test_分组维度不能直接打在模板上(client: TestClient) -> None:
    headers = _headers(client)
    dimension = _tag(client, headers, "分组维度")
    _tag(client, headers, "底下的取值", dimension)
    key = _spec(client, headers, "打标拒绝")
    response = client.patch(
        f"{API}/{key}/tags", json={"tagIds": [dimension]}, headers=headers
    )
    assert response.status_code == 400
    assert "分组维度" in response.json()["message"]


def test_按标签筛选同维度OR跨维度AND(client: TestClient) -> None:
    headers = _headers(client)
    usage = _tag(client, headers, "轴A")
    a1, a2 = _tag(client, headers, "A1", usage), _tag(client, headers, "A2", usage)
    style = _tag(client, headers, "轴B")
    b1 = _tag(client, headers, "B1", style)

    keys = [_spec(client, headers, f"筛选{i}") for i in range(3)]
    for key, tags in ((keys[0], [a1, b1]), (keys[1], [a2]), (keys[2], [])):
        response = client.patch(
            f"{API}/{key}/tags", json={"tagIds": tags}, headers=headers
        )
        assert response.status_code == 200, response.text

    # 同维度两个取值：OR，前两条都命中
    got = client.get(f"{API}?tagIds={a1}&tagIds={a2}", headers=headers).json()["data"]
    assert {item["key"] for item in got["items"]} == {keys[0], keys[1]}
    assert got["total"] == 2
    # 跨维度：AND，只有同时挂了 A 轴取值与 B 轴取值的那条。
    # keys[1] 只有 a2、keys[2] 空，都不该出现。
    got = client.get(f"{API}?tagIds={a1}&tagIds={b1}", headers=headers).json()["data"]
    assert {item["key"] for item in got["items"]} == {keys[0]}
    # a2 + b1 是不同条的拼接，不该"凑出"一条
    got = client.get(f"{API}?tagIds={a2}&tagIds={b1}", headers=headers).json()["data"]
    assert got["total"] == 0


def test_删标签清模板绑定但保留提升后的下级(client: TestClient) -> None:
    headers = _headers(client)
    dimension = _tag(client, headers, "待删维度")
    child = _tag(client, headers, "保留的取值", dimension)
    key = _spec(client, headers, "删标签")
    # 有下级的一级维度不能直接打；只打它的子级
    rejected = client.patch(
        f"{API}/{key}/tags", json={"tagIds": [dimension]}, headers=headers
    )
    assert rejected.status_code == 400
    client.patch(f"{API}/{key}/tags", json={"tagIds": [child]}, headers=headers)
    resp = client.get(f"{API}/{key}", headers=headers)
    assert child in resp.json()["data"]["tagIds"]

    deleted = client.delete(f"{API}/tags/{dimension}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    after = client.get(f"{API}/{key}", headers=headers).json()["data"]
    assert dimension not in after["tagIds"]
    # 下级提升一级后 id 不变、仍然挂在模板上
    assert child in after["tagIds"]
    tags = {item["id"]: item for item in client.get(f"{API}/tags", headers=headers).json()["data"]}
    assert tags[child]["depth"] == 1


def test_叶子取值移到另一个维度下不报500(client: TestClient) -> None:
    """二级取值是叶子，没有下级 —— 移动时算子树高度不能对空列表取 max。"""
    headers = _headers(client)
    dim_a = _tag(client, headers, "原维度")
    dim_b = _tag(client, headers, "目标维度")
    value = _tag(client, headers, "可移动取值", dim_a)

    moved = client.patch(
        f"{API}/tags/{value}/parent", json={"parentId": dim_b}, headers=headers
    )
    assert moved.status_code == 200, moved.text
    data = moved.json()["data"]
    assert data["parentId"] == dim_b
    assert data["parentName"] == next(
        item["name"] for item in client.get(f"{API}/tags", headers=headers).json()["data"]
        if item["id"] == dim_b
    )
    assert data["depth"] == 2
