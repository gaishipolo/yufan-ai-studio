"""视频模板的原片可以来自资产库 v2。

两套 id 都是 32 位 hex，`source_video_asset_id` 一列必然混装，所以写入端必须
**显式**接收「这个 id 属于哪一侧」（`sourceVideoIdSpace`），并且：

1. 名字与时长以资产库那一行为准——封装卡上写的是库里的事实，前端手里的可能
   是上一次取详情时的旧值；
2. 空间要跟着 id 一起落库，**改提示词那条路径（出参 → 行）不能把它冲回 v1**；
3. 老调用不传这个字段时行为一个字不变（默认 v1）。

这三条任一漏掉都是「成功但静默错」：模板看着封装好了，生成时按另一侧解释 id，
最后报「原片不存在」或干脆取了另一条片。
"""

from __future__ import annotations

import hashlib
import uuid

from fastapi.testclient import TestClient

from db.session import get_session_factory
from modules.library.models import LibraryAsset
from modules.template.models import VideoTemplate

OWNER = "u_001"


def _row(template_id: str) -> VideoTemplate:
    async def go() -> VideoTemplate:
        async with get_session_factory()() as session:
            row = await session.get(VideoTemplate, template_id)
            assert row is not None
            return row

    import asyncio

    return asyncio.run(go())


def _library_video(
    name: str,
    *,
    duration: float = 4.0,
    has_audio: bool = True,
    kind: str = "video",
    visibility: str = "personal",
    owner: str = OWNER,
) -> str:
    """造一条资产库行。

    **不走上传接口**：上传要过 ffprobe，假字节会被判成不认识的容器。这里只需要
    元数据（kind / 名字 / 时长 / 音轨），字节由生成阶段自己取。
    """
    content = uuid.uuid4().bytes
    asset_id = uuid.uuid4().hex

    async def go() -> None:
        async with get_session_factory()() as session:
            session.add(
                LibraryAsset(
                    id=asset_id,
                    name=name,
                    kind=kind,
                    origin="template_source",
                    mime_type="video/mp4" if kind == "video" else "image/png",
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    storage_key=f"library/blobs/{asset_id}.mp4",
                    duration=duration,
                    has_audio=has_audio,
                    width=1080,
                    height=1920,
                    owner_id=owner,
                    visibility=visibility,
                    state="active",
                    state_version=1,
                )
            )
            await session.commit()

    import asyncio

    asyncio.run(go())
    return asset_id


def _draft(source_id: str, **overrides: object) -> dict[str, object]:
    """只声明槽位的草稿：不绑包，服务端按用途生成媒体清单。"""
    draft: dict[str, object] = {
        "name": "资产库原片模板",
        "templateKey": uuid.uuid4().hex[:12],
        "sourceVideoAssetId": source_id,
        "sourceVideoName": "前端手里的旧名字.mp4",
        "sourceDurationSec": 12.0,
        "defaultKind": "product",
        "prompt": "把 {{Mixed 1}} 里的产品换成 {{Mixed 2}}。",
        "rules": {
            "referenceBindings": {"{{Mixed 1}}": "source_video", "{{Mixed 2}}": "front"}
        },
    }
    draft.update(overrides)
    return draft


def test_create_takes_name_and_duration_from_the_library_row(
    client: TestClient, api: str
) -> None:
    source_id = _library_video("资产库里的原片.mp4", duration=6.0)
    response = client.post(
        f"{api}/templates/video-templates",
        json=_draft(source_id, sourceVideoIdSpace="v2"),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["sourceVideoAssetId"] == source_id
    assert data["sourceVideoIdSpace"] == "v2"
    # 库里是 6 秒、名字是资产库那一行的：前端传的 12 秒与旧名字都不该胜出
    assert data["sourceVideoName"] == "资产库里的原片.mp4"
    assert data["sourceDurationSec"] == 6.0
    # 档位按真实时长取整，不是按前端传的 12
    assert data["generation"]["duration"] == 6
    assert _row(data["id"]).source_video_asset_id_space == "v2"


def test_old_callers_keep_landing_on_v1(client: TestClient, api: str) -> None:
    """不传 `sourceVideoIdSpace` 的调用一个字都不用改：行上仍是 v1。"""
    v1_id = uuid.uuid4().hex
    response = client.post(
        f"{api}/templates/video-templates",
        json=_draft(v1_id, sourceVideoName="旧库原片.mp4", sourceDurationSec=3.0),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["sourceVideoIdSpace"] == "v1"
    assert _row(data["id"]).source_video_asset_id_space == "v1"


def test_unknown_v2_source_is_rejected(client: TestClient, api: str) -> None:
    response = client.post(
        f"{api}/templates/video-templates",
        json=_draft(uuid.uuid4().hex, sourceVideoIdSpace="v2"),
    )
    assert response.status_code == 400, response.text
    # 「不存在」与「不可读」同一句：不能告诉调用方这条素材确实存在
    assert "原片不存在或已停止共享" in response.text


def test_image_cannot_be_a_source_video(client: TestClient, api: str) -> None:
    image_id = _library_video("一张图.png", kind="image")
    response = client.post(
        f"{api}/templates/video-templates",
        json=_draft(image_id, sourceVideoIdSpace="v2"),
    )
    assert response.status_code == 400, response.text
    assert "视频原片" in response.text


def test_attach_source_switches_space_and_prompt_edit_keeps_it(
    client: TestClient, api: str
) -> None:
    """换原片要连空间一起换；改提示词不能把它冲回 v1。

    改提示词走的是「出参 → 行」（`apply_out`），空间列漏在 `VideoTemplateOut`
    之外时，一次保存就会把 v2 原片重新解释成 v1——表现是之后每次生成都报
    「原片不存在」，而用户什么都没做错。
    """
    first = _library_video("第一支.mp4", duration=3.0)
    created = client.post(
        f"{api}/templates/video-templates",
        json=_draft(first, sourceVideoIdSpace="v2"),
    )
    assert created.status_code == 200, created.text
    template_id = created.json()["data"]["id"]

    second = _library_video("第二支.mp4", duration=5.0)
    attached = client.post(
        f"{api}/templates/video-templates/{template_id}/attach-source",
        json={"sourceVideoAssetId": second, "sourceVideoIdSpace": "v2"},
    )
    assert attached.status_code == 200, attached.text
    data = attached.json()["data"]
    assert data["sourceVideoAssetId"] == second
    assert data["sourceVideoName"] == "第二支.mp4"
    assert data["sourceDurationSec"] == 5.0
    assert data["sourceVideoIdSpace"] == "v2"

    saved = client.patch(
        f"{api}/templates/video-templates/{template_id}/prompt",
        json={"prompt": "把 {{Mixed 1}} 里的产品换成 {{Mixed 2}}，保持分镜。"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["sourceVideoIdSpace"] == "v2"
    assert _row(template_id).source_video_asset_id_space == "v2"
