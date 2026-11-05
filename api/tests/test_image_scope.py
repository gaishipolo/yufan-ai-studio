"""生图历史的数据范围（R4-07 / R6-09）。

与任务列表同一套规则：能不能看到别人的生图历史，只看 `image:read:all`。
没有这个开关的时候，管理员在生图历史里也只看得到自己那几条，
而任务列表里能看到全站——同一个工作台两套答案，用户只会以为是 bug。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

API = "/api/v1"


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post(f"{API}/identity/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}


def test_list_only_shows_own_history_without_read_all(client: TestClient) -> None:
    """种子里的生图记录都归 `u_001`：没有 `image:read:all` 的账号看不到它们。"""
    viewer = _login(client, "viewer", "viewer@12345")  # 只有 image:read
    data = client.get(f"{API}/images", headers=viewer).json()["data"]

    assert data["items"] == []
    assert data["total"] == 0


def test_list_shows_all_history_with_read_all(client: TestClient) -> None:
    """超管是 `*`，自然覆盖 `image:read:all`：能看到别人产出的历史。"""
    admin = _login(client, "admin", "admin@12345")
    data = client.get(f"{API}/images", headers=admin).json()["data"]

    assert data["total"] >= 1
    assert all(item["id"] for item in data["items"])


def test_history_is_paginated_and_total_is_a_real_count(client: TestClient) -> None:
    """`GET /images` 必须分页，且 `total` 是全量计数（R7-02）。

    范围打开之后，这个接口对 `image:*` 的账号就是全表返回；`total` 用 `len(rows)`
    的话，有 30 条、这页 10 条时会报「共 10 条」——分页器和总数对不上，前端翻到
    第二页就翻不动了。
    """
    import asyncio

    from db.session import get_session_factory
    from modules.image.models import ImageJob

    maker = "pager"
    extra = 30

    async def _seed_many() -> None:
        async with get_session_factory()() as session:
            session.add_all(
                [
                    ImageJob(
                        name=f"{maker}-{i}",
                        prompt=f"分页用例 {i}",
                        size="1024x1536",
                        cover_url="",
                        owner_id="u_001",
                    )
                    for i in range(extra)
                ]
            )
            await session.commit()

    asyncio.run(_seed_many())
    admin = _login(client, "admin", "admin@12345")

    first = client.get(f"{API}/images?page=1&pageSize=10", headers=admin).json()["data"]
    assert len(first["items"]) == 10
    assert first["page"] == 1
    assert first["pageSize"] == 10
    assert first["total"] >= extra  # 种子里的历史 + 刚插入的

    last = client.get(
        f"{API}/images?page={first['total'] // 10 + 2}&pageSize=10", headers=admin
    ).json()["data"]
    assert last["items"] == []
    assert last["total"] == first["total"]  # 翻过尾页不会把总数清零
