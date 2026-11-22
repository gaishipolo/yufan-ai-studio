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


def test_reference_limits_are_per_kind(client: TestClient, api: str) -> None:
    """按素材类型分开的上限要出现在目录里。

    只给总数是不够的：Agnes 视频是「5 张图 + 3 段音频 = 总数 8」，前端只守总数
    的话，用户加到第 6 张图时界面还说「还能再加」，提交才吃 400。
    """
    data = client.get(f"{api}/models", params={"capability": "video"}).json()["data"]
    agnes = next(item for item in data["items"] if item["id"] == "agnes-video-2.5-flash")

    assert agnes["referenceKinds"] == ["image", "audio"]
    assert agnes["referenceLimits"]["image"]["maxCount"] == 5
    assert agnes["referenceLimits"]["image"]["minSide"] == 256
    assert agnes["referenceLimits"]["image"]["maxSide"] == 5760
    assert agnes["referenceLimits"]["audio"]["maxCount"] == 3

    # 总数 = 各类之和，两个上限要能对上；对不上就是某处声明漂了
    total = sum(limit["maxCount"] for limit in agnes["referenceLimits"].values())
    assert total == agnes["maxReferences"]


def test_models_without_declared_limits_stay_empty(client: TestClient, api: str) -> None:
    """没声明按类上限的供应商给空对象（不是猜一个）。

    替换模型（LibTV / 百炼）只声明了总数与类型，前端据此退回「只守总数」。
    """
    data = client.get(f"{api}/models", params={"capability": "video"}).json()["data"]
    libtv = next(item for item in data["items"] if item["id"] == "libtv-wan3-replacement")

    # 「声明了类型」与「声明了每类几份」是两件事：前者有，后者没有。
    # 断言成 `== {}` 时也顺手钉住这一点，否则「字段默认空」和「真的没声明」
    # 在出参上长得一模一样，谁也没法从接口看出是没接上还是本来就没有。
    assert libtv["referenceKinds"]
    assert libtv["referenceLimits"] == {}
