"""两条链按**供应商声明**分流参考素材（不是写死内联）。"""

from __future__ import annotations

from typing import Any

import pytest

from core.exceptions import BadRequest
from generation.spec import GenerationRecord
from infra.media_channel import MediaChannel
from modules.image.pipeline import ImageGenerationPipeline
from modules.task.pipeline import VideoGenerationPipeline


class _Router:
    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def provider_for(self, model: str) -> Any:
        return self._provider


class _Provider:
    name = "p"

    def __init__(self, **attrs: Any) -> None:
        for key, value in attrs.items():
            setattr(self, key, value)


class _FakeStorage:
    def __init__(self) -> None:
        self.reads: list[str] = []

    async def get(self, key: str) -> bytes | None:
        self.reads.append(key)
        return b"png-bytes"


def _record(**kwargs: Any) -> GenerationRecord:
    base: dict[str, Any] = {
        "id": "gen-1",
        "kind": "image",
        "model": "m-1",
        "prompt": "画一只猫",
        "status": "pending",
        "owner_id": "u-1",
        "params": {},
        "reference_urls": ("refs/a.png",),
    }
    base.update(kwargs)
    return GenerationRecord(**base)


async def test_image_chain_inlines_when_driver_declares_base64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider(
        accepted_media_formats={"image": frozenset({MediaChannel.BASE64})}
    )
    storage = _FakeStorage()
    monkeypatch.setattr("infra.get_model_router", lambda: _Router(provider))
    monkeypatch.setattr("infra.reference_inline.get_storage", lambda: storage)

    request = await ImageGenerationPipeline().build_request(_record())

    assert len(request.reference_urls) == 1
    assert request.reference_urls[0].startswith("data:image/png;base64,")
    assert storage.reads == ["refs/a.png"]
    # 选路结果随请求一起传下去 → 提交台账能落「这次实际走了哪条通道」
    assert request.channel_used == "base64"


async def test_image_chain_rejects_driver_without_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """以前图片链写死内联，声明形同虚设；现在没声明就报错，不静默丢素材。"""
    storage = _FakeStorage()
    monkeypatch.setattr("infra.get_model_router", lambda: _Router(_Provider()))
    monkeypatch.setattr("infra.reference_inline.get_storage", lambda: storage)

    with pytest.raises(BadRequest, match="没有为 image 声明任何素材通道"):
        await ImageGenerationPipeline().build_request(_record())
    assert storage.reads == []


async def test_video_chain_keeps_material_out_of_the_body_for_workspace_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工作区通道（LibTV 那种）：素材不内联，由物化器落工作区。"""
    provider = _Provider(
        accepted_media_formats={"image": frozenset({MediaChannel.WORKSPACE})}
    )
    storage = _FakeStorage()
    monkeypatch.setattr("modules.task.pipeline.get_model_router", lambda: _Router(provider))
    monkeypatch.setattr("infra.reference_inline.get_storage", lambda: storage)

    request = await VideoGenerationPipeline().build_request(
        _record(kind="video", model="m-v")
    )

    assert request.reference_urls == ()
    assert storage.reads == []
    assert request.channel_used == "workspace"


async def test_video_chain_inlines_when_driver_declares_base64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider(
        accepted_media_formats={
            "image": frozenset({MediaChannel.BASE64}),
            "audio": frozenset({MediaChannel.BASE64}),
        }
    )
    storage = _FakeStorage()
    monkeypatch.setattr("modules.task.pipeline.get_model_router", lambda: _Router(provider))
    monkeypatch.setattr("infra.reference_inline.get_storage", lambda: storage)

    request = await VideoGenerationPipeline().build_request(
        _record(kind="video", model="m-v")
    )

    assert len(request.reference_urls) == 1
    assert request.reference_urls[0].startswith("data:image/png;base64,")
    assert storage.reads == ["refs/a.png"]
    assert request.channel_used == "base64"
