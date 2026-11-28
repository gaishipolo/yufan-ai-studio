"""模板发布闸的 seam：三档检查矩阵与顺序。

这些用例只穿过 `review_draft` / `review_publish`，不碰 HTTP；HTTP 接线由
`test_template_spec_api.py` 等端点用例覆盖。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import modules.template_pack.spec_catalog as spec_catalog
import modules.template_pack.spec_gate as spec_gate
from core.ports.library_lookup import (
    LibraryAssetKind,
    LibraryAssetRef,
    LibraryAssetState,
    LibraryTextRef,
    LibraryVisibility,
)
from core.ports.model_generation import ModelSummary
from modules.template_pack.spec_gate import review_draft, review_publish
from modules.template_pack.spec_schema import TemplateSpecIn

MODEL = "fake/image"
ACTOR = SimpleNamespace(id="actor-1")


class FakeModels:
    def __init__(self, *, capability: str = "image", max_references: int = 5) -> None:
        self._model = ModelSummary(
            id=MODEL,
            label="Fake Image",
            capability=capability,
            provider="fake",
            max_references=max_references,
        )

    def models(self, capability: str = "") -> tuple[ModelSummary, ...]:
        return (self._model,) if not capability or capability == self._model.capability else ()


class FakeLookup:
    def __init__(
        self,
        *,
        refs: dict[str, LibraryAssetRef] | None = None,
        texts: dict[str, LibraryTextRef] | None = None,
        readable: set[str] | None = None,
    ) -> None:
        self.refs = refs or {}
        self.texts = texts or {}
        self.readable = readable if readable is not None else set(self.refs) | set(self.texts)

    async def get_many(self, session, asset_ids):  # noqa: ANN001
        return {key: value for key, value in self.refs.items() if key in asset_ids}

    async def readable_ids(self, session, actor, asset_ids):  # noqa: ANN001
        return {item for item in asset_ids if item in self.readable}

    async def read_text_many(self, session, actor, asset_ids):  # noqa: ANN001
        return {
            key: value
            for key, value in self.texts.items()
            if key in asset_ids and key in self.readable
        }


def _payload(
    *,
    prompt: str = "主图 {{slot:hero}}",
    slots: list[dict] | None = None,
) -> TemplateSpecIn:
    return TemplateSpecIn.model_validate(
        {
            "key": "gate-test",
            "name": "发布闸测试",
            "model": MODEL,
            "params": {},
            "prompt": prompt,
            "slots": slots
            or [
                {
                    "key": "hero",
                    "type": "image",
                    "binding": "user",
                    "tagIds": ["tag-1"],
                    "minCount": 1,
                    "maxCount": 1,
                    "position": 0,
                    "label": "主图",
                }
            ],
        }
    )


def _image_ref(
    asset_id: str,
    *,
    visibility: LibraryVisibility = LibraryVisibility.TEAM,
    purpose: str = "",
) -> LibraryAssetRef:
    return LibraryAssetRef(
        id=asset_id,
        owner_id="actor-1",
        kind=LibraryAssetKind.IMAGE,
        name="固定图",
        state=LibraryAssetState.ACTIVE,
        visibility=visibility,
        purpose=purpose,
    )


def _text_ref(asset_id: str, content: str) -> LibraryTextRef:
    return LibraryTextRef(
        id=asset_id,
        name="固定文案",
        version=1,
        sha256="sha",
        content=content,
        size_bytes=len(content.encode()),
    )


@pytest.fixture(autouse=True)
def _fake_ports(monkeypatch: pytest.MonkeyPatch) -> FakeLookup:
    lookup = FakeLookup()
    monkeypatch.setattr(spec_catalog, "get_model_generation", lambda: FakeModels())
    monkeypatch.setattr(spec_gate, "get_library_lookup", lambda: lookup)
    return lookup


def test_draft允许超长_发布拒绝() -> None:
    # image 能力上限 2000；草稿可以先把作者的长文存下来，发布前必须拦住。
    payload = _payload(prompt="长" * 2100 + " {{slot:hero}}")
    draft = asyncio.run(review_draft(None, ACTOR, payload))
    publish = asyncio.run(review_publish(None, ACTOR, payload))

    assert draft.ok
    assert not publish.ok
    assert publish.blockers[0].code == "not_executable"
    assert "超过执行层上限" in publish.blockers[0].message


def test_固定文本内联后超长_发布拒绝() -> None:
    # 原文只有一小段引用；固定文本内联后才超上限，顺序错了就会放行。
    payload = _payload(
        prompt="开头 {{slot:txt}}",
        slots=[
            {
                "key": "txt",
                "type": "text",
                "binding": "fixed",
                "refMode": "attachment",
                "assetId": "text-1",
                "position": 0,
                "label": "固定文案",
            }
        ],
    )
    spec_gate.get_library_lookup = lambda: FakeLookup(  # type: ignore[assignment]
        texts={"text-1": _text_ref("text-1", "长" * 2100)}
    )

    draft = asyncio.run(review_draft(None, ACTOR, payload))
    publish = asyncio.run(review_publish(None, ACTOR, payload))

    assert draft.ok
    assert not publish.ok
    assert publish.blockers[0].code == "not_executable"


def test_发布要求固定素材全员可读_草稿只要求作者可读() -> None:
    payload = _payload(
        prompt="主图 {{slot:hero}}",
        slots=[
            {
                "key": "hero",
                "type": "image",
                "binding": "fixed",
                "assetId": "private-1",
                "position": 0,
                "label": "主图",
            }
        ],
    )
    lookup = FakeLookup(
        refs={
            "private-1": _image_ref(
                "private-1", visibility=LibraryVisibility.PERSONAL
            )
        }
    )
    spec_gate.get_library_lookup = lambda: lookup  # type: ignore[assignment]

    assert asyncio.run(review_draft(None, ACTOR, payload)).ok
    report = asyncio.run(review_publish(None, ACTOR, payload))
    assert not report.ok
    assert report.blockers[0].code == "fixed_asset_unreadable"
    assert "只有你自己能看到" in report.blockers[0].message


def test_发布阻塞保留结构化_details(monkeypatch: pytest.MonkeyPatch) -> None:
    """经 gate 转成 HTTP 400 后，`details` 不能只剩一句话。"""

    class LockedModels(FakeModels):
        def __init__(self) -> None:
            super().__init__()
            self._model = ModelSummary(
                id=MODEL,
                label="Fake Image",
                capability="image",
                provider="fake",
                max_references=5,
                param_schema={
                    "quality": {"label": "质量", "userSelectable": True}
                },
            )

    monkeypatch.setattr(spec_catalog, "get_model_generation", lambda: LockedModels())
    payload = _payload().model_copy(update={"params": {"quality": "hd"}})

    report = asyncio.run(review_draft(None, ACTOR, payload))

    assert not report.ok
    assert report.blockers[0].code == "model_invalid"
    assert report.blockers[0].details == {"userSelectableParams": ["quality"]}


def test_草稿也拦固定文本读不到() -> None:
    payload = _payload(
        prompt="开头 {{slot:txt}}",
        slots=[
            {
                "key": "txt",
                "type": "text",
                "binding": "fixed",
                "refMode": "attachment",
                "assetId": "text-1",
                "position": 0,
                "label": "固定文案",
            }
        ],
    )
    spec_gate.get_library_lookup = lambda: FakeLookup(texts={})  # type: ignore[assignment]

    report = asyncio.run(review_draft(None, ACTOR, payload))
    assert not report.ok
    assert report.blockers[0].code == "fixed_text_unreadable"
