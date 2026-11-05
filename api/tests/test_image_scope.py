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
