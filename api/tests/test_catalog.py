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
    # 占位模型（echo 的 `seedance-2.5`）**不在**公开目录里——它实现是假的，
    # 露脸会让用户选中后拿到 `example.com/mock/` 的占位图且全程不报错。
    assert "seedance-2.5" not in ids
    assert "agnes-video-2.5-flash" in ids
    # 每个能力恰好一个默认模型
    assert sum(1 for item in data["items"] if item["isDefault"]) == 1

    video = next(item for item in data["items"] if item["id"] == "agnes-video-2.5-flash")
    assert video["paramSchema"]["durationSec"]["max"] == 12
    assert video["provider"] == "agnes_video"


def test_lists_all_capabilities(client: TestClient, api: str) -> None:
    data = client.get(f"{api}/models").json()["data"]
    capabilities = {item["capability"] for item in data["items"]}
    # video / image 有真实供应商；audio 目前**只有**占位模型（echo 的 `tts-v3`），
    # 所以它不在目录里 —— 等接入真实 TTS 供应商后这一条会自动放宽。
    assert {"video", "image"} <= capabilities
    assert "audio" not in capabilities


def test_unknown_capability_is_rejected(client: TestClient, api: str) -> None:
    resp = client.get(f"{api}/models", params={"capability": "hologram"})
    assert resp.status_code == 422
