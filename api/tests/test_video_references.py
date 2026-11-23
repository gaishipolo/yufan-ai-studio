"""视频任务的参考素材：受理期的类型 / 张数 / 边长守卫 + 链路层的内联。

## 为什么要有这个文件

参考素材这条链上有三道闸门，缺一道就会以很难查的方式坏掉：

1. **类型**：能收哪几类由**模型声明**（`referenceKinds`）。Agnes 视频只收图与
   音轨，视频替换模型收「原片 + 图 + 音频」。受理期按声明筛，声明与上游
   不一致时，用户会拿到一句「类型不受支持」而不知道自己选错了模型。
2. **边长**：图片单边必须在 256-5760 像素。受理期按资产库里的宽高判一次 ——
   判不出来（宽高为 0 的历史行）就放行，提交前还有一道按真实字节的检查。
3. **内联**：上游只收公网地址或 `data:` URI，我们的存储在内网、给不出公网
   地址，所以链路层必须把资产库的字节读成 data URI。

少一道，用户看到的都是「传了参考图，但成片跟参考图没关系」。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from library_support import admin_headers, png_bytes

API = "/api/v1"
AGNES_VIDEO = "agnes-video-2.5-flash"


def _upload_png(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str = "ref.png",
    size: tuple[int, int] = (512, 512),
) -> dict[str, Any]:
    """上传一张真实 PNG。尺寸可选：边长守卫要用它打边界。"""
    response = client.post(
        f"{API}/library/assets/upload",
        content=png_bytes("teal", size),
        params={"filename": name, "kind": "image"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _create_task(
    client: TestClient,
    headers: dict[str, str],
    reference_ids: list[str],
    *,
    model: str = AGNES_VIDEO,
) -> Any:
    return client.post(
        f"{API}/tasks",
        json={
            "prompt": "让参考素材里的角色动起来",
            "model": model,
            "referenceLibraryIds": reference_ids,
        },
        headers=headers,
    )


def test_reference_image_is_accepted_by_agnes_video(client: TestClient) -> None:
    """以前这里必 400：模型声明 `maxReferences=0`，受理期直接拒。"""
    headers = admin_headers(client)
    asset = _upload_png(client, headers)

    response = _create_task(client, headers, [asset["id"]])

    assert response.status_code == 201, response.text
    assert response.json()["data"]["referenceLibraryIds"] == [asset["id"]]


def test_reference_image_that_is_too_small_is_rejected_at_acceptance(
    client: TestClient,
) -> None:
    """边长越界当场 400：异步失败的话用户只能看到一句英文报错。"""
    headers = admin_headers(client)
    asset = _upload_png(client, headers, name="small.png", size=(64, 64))

    response = _create_task(client, headers, [asset["id"]])

    assert response.status_code == 400, response.text
    assert "边长" in response.text


def test_too_many_references_are_rejected_at_acceptance(client: TestClient) -> None:
    """张数上限按模型声明（5 张图 + 3 段音频 = 8 份）。"""
    headers = admin_headers(client)

    response = _create_task(
        client, headers, [f"missing-{index}" for index in range(9)]
    )

    assert response.status_code == 400, response.text
    assert "8" in response.text


def test_reference_is_inlined_into_the_generation_request(client: TestClient) -> None:
    """链路层把资产库的字节读成 data URI —— 上游给不出公网地址。"""
    headers = admin_headers(client)
    asset = _upload_png(client, headers)
    created = _create_task(client, headers, [asset["id"]]).json()["data"]

    async def run() -> tuple[str, ...]:
        from db.session import get_session_factory
        from modules.task.pipeline import VideoGenerationPipeline, VideoTaskStore

        async with get_session_factory()() as session:
            record = await VideoTaskStore(session).load(created["id"])
            assert record is not None
            request = await VideoGenerationPipeline().build_request(record)
            return request.reference_urls

    uris = asyncio.run(run())

    assert len(uris) == 1
    assert uris[0].startswith("data:image/png;base64,")


def test_reference_kinds_come_from_the_model_declaration() -> None:
    """能收哪几类由模型说了算：Agnes 视频不收视频，替换模型三类都收。"""
    from core.ports.library_lookup import LibraryAssetKind
    from modules.model_driver.drivers.agnes_video import MODELS as AGNES_VIDEO_MODELS
    from modules.model_driver.drivers.libtv import MODELS as LIBTV_MODELS
    from modules.task.service import _reference_kinds

    agnes = next(item for item in AGNES_VIDEO_MODELS if item.id == AGNES_VIDEO)
    assert _reference_kinds(agnes) == (
        LibraryAssetKind.IMAGE,
        LibraryAssetKind.AUDIO,
    )

    libtv = next(item for item in LIBTV_MODELS if item.replacement)
    assert _reference_kinds(libtv) == (
        LibraryAssetKind.IMAGE,
        LibraryAssetKind.VIDEO,
        LibraryAssetKind.AUDIO,
    )


# --------------------------------------------------- 官方「参考媒体限制」的那张表
#
# Agnes Video 2.5 Flash 的页面上只列「与 2.5 的差异」（图片 5 张 / 音频 3 段 /
# 不收视频），体积与时长写在 2.5 的「参考媒体限制」里，Flash 全盘沿用：
#
# | 图片 | 单张 < 15MB · 整单 < 50MB · 宽高各 256-5760px |
# | 音频 | 总时长 2-12 秒 · 单段 < 15MB · 整单 < 64MB |
#
# 这里逐条钉住。**阈值必须双向测**：只测「16MB 被拒」的话，上限写成 6MB 也会
# 绿 —— 文案里那个数字才是判据。


def _insert_audio(
    owner_id: str,
    *,
    duration: float,
    name: str = "参考音.mp3",
    size_bytes: int = 1024,
) -> str:
    from library_support import insert_asset

    return insert_asset(
        name=name,
        kind="audio",
        mime_type="audio/mpeg",
        duration=duration,
        size_bytes=size_bytes,
        storage_key=f"tests/{name}",
        owner_id=owner_id,
        state="active",
    )


def _insert_image(
    owner_id: str,
    *,
    size_bytes: int,
    name: str = "参考图.png",
    side: int = 512,
) -> str:
    from library_support import insert_asset

    return insert_asset(
        name=name,
        kind="image",
        mime_type="image/png",
        width=side,
        height=side,
        size_bytes=size_bytes,
        storage_key=f"tests/{name}",
        owner_id=owner_id,
        state="active",
    )


def _owner_id(client: TestClient, headers: dict[str, str]) -> str:
    return client.get(f"{API}/identity/me", headers=headers).json()["data"]["id"]


def test_audio_shorter_than_two_seconds_is_rejected(client: TestClient) -> None:
    """1.5 秒：兜底的 1 秒口径会放行，Agnes 的 2 秒下限必须拒。"""
    headers = admin_headers(client)
    asset = _insert_audio(_owner_id(client, headers), duration=1.5)

    response = _create_task(client, headers, [asset])

    assert response.status_code == 400, response.text
    assert "时长不足 2 秒" in response.json()["message"]


def test_audio_total_duration_over_twelve_seconds_is_rejected(
    client: TestClient,
) -> None:
    """三段各 5 秒：单段都合法，合计 15 秒超过 12 —— 逐段查不出来。"""
    headers = admin_headers(client)
    owner = _owner_id(client, headers)
    assets = [
        _insert_audio(owner, duration=5.0, name=f"参考音{i}.mp3") for i in range(3)
    ]

    response = _create_task(client, headers, assets)

    assert response.status_code == 400, response.text
    assert "总时长不能超过 12 秒" in response.json()["message"]


def test_audio_total_duration_within_twelve_seconds_is_accepted(
    client: TestClient,
) -> None:
    """两段各 5 秒（合计 10 秒）该放行 —— 别把合计上限判成单段上限。"""
    headers = admin_headers(client)
    owner = _owner_id(client, headers)
    assets = [
        _insert_audio(owner, duration=5.0, name=f"参考音{i}.mp3") for i in range(2)
    ]

    response = _create_task(client, headers, assets)

    assert response.status_code == 201, response.text


def test_image_of_seven_megabytes_is_accepted(client: TestClient) -> None:
    """7MB：按旧的 6MB 上限会被拒，提到 15MB 后必须放行。"""
    headers = admin_headers(client)
    asset = _insert_image(_owner_id(client, headers), size_bytes=7 * 1024 * 1024)

    response = _create_task(client, headers, [asset])

    assert response.status_code == 201, response.text


def test_image_over_fifteen_megabytes_is_rejected(client: TestClient) -> None:
    headers = admin_headers(client)
    asset = _insert_image(_owner_id(client, headers), size_bytes=16 * 1024 * 1024)

    response = _create_task(client, headers, [asset])

    assert response.status_code == 400, response.text
    assert "超过 15MB" in response.json()["message"]


def test_image_total_over_fifty_megabytes_is_rejected(client: TestClient) -> None:
    """五张各 12MB：每张都在 15MB 以内，加起来 60MB 超过整单 50MB。"""
    headers = admin_headers(client)
    owner = _owner_id(client, headers)
    assets = [
        _insert_image(owner, size_bytes=12 * 1024 * 1024, name=f"参考图{i}.png")
        for i in range(5)
    ]

    response = _create_task(client, headers, assets)

    assert response.status_code == 400, response.text
    assert "合计不能超过 50MB" in response.json()["message"]
