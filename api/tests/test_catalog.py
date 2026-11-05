"""模型目录接口：前端选择器的数据源。

存在的意义是让「新增模型」不需要改前端常量。
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_lists_video_models_with_schema(client: TestClient, api: str) -> None:
    data = client.get(f"{api}/models", params={"capability": "video"}).json()["data"]

    assert data["total"] == len(data["items"]) >= 1
    assert all(item["capability"] == "video" for item in data["items"])

    ids = {item["id"] for item in data["items"]}
    assert "seedance-2.5" in ids
    # 每个能力恰好一个默认模型
    assert sum(1 for item in data["items"] if item["isDefault"]) == 1

    seedance = next(item for item in data["items"] if item["id"] == "seedance-2.5")
    assert seedance["paramSchema"]["durationSec"]["max"] == 12
    assert seedance["provider"] == "echo"


def test_lists_all_capabilities(client: TestClient, api: str) -> None:
    data = client.get(f"{api}/models").json()["data"]
    capabilities = {item["capability"] for item in data["items"]}
    assert {"video", "image", "audio"} <= capabilities


def test_unknown_capability_is_rejected(client: TestClient, api: str) -> None:
    resp = client.get(f"{api}/models", params={"capability": "hologram"})
    assert resp.status_code == 422
