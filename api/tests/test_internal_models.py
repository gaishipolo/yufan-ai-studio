"""`internal=True` 的模型：不在目录里露脸，但仍能被按 id 解析。

背景（T33）：替换链的 `bailian-wan3-*` / `libtv-wan3-*` 提交时要求 `params`
里带 `archiveRunId`。它们出现在通用生图 / 视频页的下拉里时，用户选中必然
400「调用档案标识不匹配」——报错完全指不到「这个模型不该出现在这里」。

所以两件事必须同时成立，缺一条就是 bug：

1. 给人挑的地方（`GET /models`、门面的模型清单）看不到它们；
2. 按 id 找实现的地方（`resolve()` / `provider_for()`，替换链正是这么调的）
   仍然找得到——在解析层过滤等于把替换链打断。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from infra.ai.base import Capability, GenerationProgress, GenerationRequest, ModelSpec
from infra.ai.router import ModelRouter
from modules.catalog.service import CatalogService


class _FakeProvider:
    """只满足 `register()` 需要的最小供应商。"""

    def __init__(self, name: str, specs: Sequence[ModelSpec]) -> None:
        self.name = name
        self.models = tuple(specs)
        self.capabilities = frozenset(spec.capability for spec in specs)

    async def submit(self, request: GenerationRequest) -> GenerationProgress:
        raise AssertionError("本用例不提交")

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        raise AssertionError("本用例不轮询")

    async def cancel(self, external_id: str) -> None:
        return None


def _spec(model_id: str, *, internal: bool = False) -> ModelSpec:
    return ModelSpec(
        id=model_id,
        label=model_id,
        capability=Capability.IMAGE,
        provider="fake",
        param_schema={"size": {"type": "enum", "options": ["1024x1024"]}},
        internal=internal,
    )


def _router(*specs: ModelSpec) -> ModelRouter:
    router = ModelRouter()
    router.register(_FakeProvider("fake", specs))
    return router


def test_catalog_hides_internal_models_but_resolve_keeps_them() -> None:
    router = _router(_spec("public-image"), _spec("chain-only", internal=True))

    listed = CatalogService(router).list_models(Capability.IMAGE)
    assert [item.id for item in listed.items] == ["public-image"]

    # 解析层不受影响：替换链就是拿这个 id 直接提交的
    assert router.resolve("chain-only") is not None
    assert router.provider_for("chain-only").name == "fake"
    # 巡检 / 维护仍看得到全部
    assert {spec.id for spec in router.models(Capability.IMAGE)} == {
        "public-image",
        "chain-only",
    }


def test_internal_default_model_is_not_marked_on_another_model() -> None:
    """默认值是内部模型时，**不能**把默认标记挪给别的公开模型。

    否则前端会「以为默认是 A、于是不传 model」，后端却按默认取到内部模型 B，
    直接 400。宁可一个默认都没有，让前端显式带上 id。
    """
    router = _router(_spec("chain-default", internal=True), _spec("public-image"))

    listed = CatalogService(router).list_models(Capability.IMAGE)
    assert [item.id for item in listed.items] == ["public-image"]
    assert listed.items[0].is_default is False


def test_public_default_is_still_marked() -> None:
    router = _router(_spec("public-image"), _spec("chain-only", internal=True))

    listed = CatalogService(router).list_models(Capability.IMAGE)
    assert [item.is_default for item in listed.items] == [True]


def test_replacement_channel_models_are_declared_internal() -> None:
    """真实声明：漏标一个，用户在通用页就能选中它然后 400。

    `bailian` / `libtv` 都是替换链专用（提交要带 `archiveRunId`），加新模型
    时同样要带上 `internal=True`。
    """
    from infra.ai.libtv import LibTVProvider
    from modules.model_driver.drivers import bailian

    # libtv 的清单挂在供应商类上（老路径），bailian 是驱动模块级 `MODELS`
    for label, specs in (
        ("bailian", bailian.MODELS),
        ("libtv", LibTVProvider.models),
    ):
        assert specs, f"{label} 没声明任何模型"
        for spec in specs:
            assert spec.internal, f"{label}: {spec.id} 未标记 internal"


def test_internal_flag_defaults_to_false() -> None:
    """没写这个字段的驱动保持「公开」——否则接入一个新供应商会静默从目录消失。"""
    spec: Any = _spec("public-image")
    assert spec.internal is False
