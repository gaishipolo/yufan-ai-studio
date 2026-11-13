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

from infra import get_model_router
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


def _spec(
    model_id: str,
    *,
    internal: bool = False,
    placeholder: bool = False,
    provider: str = "fake",
) -> ModelSpec:
    return ModelSpec(
        id=model_id,
        label=model_id,
        capability=Capability.IMAGE,
        provider=provider,
        param_schema={"size": {"type": "enum", "options": ["1024x1024"]}},
        internal=internal,
        placeholder=placeholder,
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
    """内部模型当不了默认，于是公开模型**正常**被点亮为默认。

    这条守卫原来写的是「默认值是内部模型时，不能把默认标记挪给别的公开
    模型」——那时的解法是「宁可一个默认都没有」，让前端显式带 id。

    现在解法更靠前一步：**内部模型根本不会被选成默认**（`_set_default_if_absent`
    跳过 `internal`）。于是「前端以为默认是 A、后端却按默认取到内部模型 B」
    这个烂局面压根不会成立——后端取的默认就是前端看到的那个 A。

    断言要跟着改口径：不是「默认标记不许出现」，而是「默认落在真实模型上」。
    """
    router = _router(_spec("chain-default", internal=True), _spec("public-image"))

    # 内部模型没进默认位
    assert router.default_model_or_none(Capability.IMAGE) == "public-image"

    listed = CatalogService(router).list_models(Capability.IMAGE)
    assert [item.id for item in listed.items] == ["public-image"]
    assert listed.items[0].is_default is True


def test_public_default_is_still_marked() -> None:
    router = _router(_spec("public-image"), _spec("chain-only", internal=True))

    listed = CatalogService(router).list_models(Capability.IMAGE)
    assert [item.is_default for item in listed.items] == [True]


def test_replacement_channel_models_are_declared_internal() -> None:
    """真实声明：漏标一个，用户在通用页就能选中它然后 400。

    `bailian` / `libtv` 都是替换链专用（提交要带 `archiveRunId`），加新模型
    时同样要带上 `internal=True`。
    """
    from modules.model_driver.drivers import bailian
    from modules.model_driver.drivers.libtv import LibTVProvider

    # libtv 的清单挂在供应商类上（老路径），bailian 是驱动模块级 `MODELS`
    for label, specs in (
        ("bailian", bailian.MODELS),
        ("libtv", LibTVProvider.models),
    ):
        assert specs, f"{label} 没声明任何模型"
        for spec in specs:
            assert spec.internal, f"{label}: {spec.id} 未标记 internal"


def test_underscore_prefixed_driver_files_are_skipped() -> None:
    """文件名带 `_` 前缀的驱动**不自动载入**。

    约定写在 `_template.py` 的文件头：「文件名以下划线开头，`registry.
    _iter_driver_modules()` 会跳过它，所以这份骨架不会被当成真驱动载入」。
    骨架守住了，**示例驱动没守住**——它们叫 `mock_async.py` / `mock_sync.py`，
    于是真被扫进来：用户在生成工作台里能选中「示例同步生图」「示例异步视频」，
    提交后只拿到 `https://example.com/mock/...` 的占位图。下拉里多出来的条目
    在真实部署里根本不该存在。

    这条守卫钉住「下划线 = 不载入」这个约定本身：新增示例驱动时若忘了加
    `_`，这里会红。
    """
    from modules.model_driver.registry import _iter_driver_modules

    discovered = {name for name, _ in _iter_driver_modules()}
    assert "_template" not in discovered
    assert "_mock_async" not in discovered
    assert "_mock_sync" not in discovered
    # 反过来：真实驱动必须在发现列表里
    assert {"agnes", "agnes_video", "bailian"} <= discovered


def test_example_driver_models_stay_out_of_public_catalog() -> None:
    """示例驱动的模型不得出现在给人挑的目录里。

    兜底断言：不依赖「文件名带 `_`」这条实现约定，直接钉住**结果**——
    用户可见的模型目录里不能有示例模型。
    """
    from infra import get_model_router
    from modules.model_driver.registry import ensure_drivers_registered

    ensure_drivers_registered()
    public_ids = {spec.id for spec in get_model_router().public_models()}
    assert not public_ids & {
        "md-sync-image",
        "md-async-image",
        "md-async-video",
    }


def test_internal_flag_defaults_to_false() -> None:
    """没写这个字段的驱动保持「公开」——否则接入一个新供应商会静默从目录消失。"""
    spec: Any = _spec("public-image")
    assert spec.internal is False


# ---------------------------------------------------------------------------
# placeholder：占位模型（实现是假的）
#
# 与 `internal` 是**两回事**，别混用：
# - `internal` = 只能自己的业务链路调（替换链专用），露脸必然 400；
# - `placeholder` = **实现是假的**（本地 echo 占位供应商），露脸不报错，
#   但用户选中后拿到的是 `example.com/mock/...` 的占位图。
#
# 混用的后果正好相反：`internal` 的模型被写成 placeholder，或反之，
# 都会让「该露脸的」或「该挡的」漏掉一个。
# ---------------------------------------------------------------------------


def test_placeholder_flag_defaults_to_false() -> None:
    """没写这个字段的模型保持「公开」——否则接入真实供应商会静默从目录消失。

    这跟 `internal` 的默认值守卫是一对：两个标记都默认 False，
    新增模型必须**显式**声明自己是哪一类。
    """
    spec: Any = _spec("public-image")
    assert spec.placeholder is False


def test_catalog_hides_placeholder_models_but_resolve_keeps_them() -> None:
    """占位模型不进目录，但仍能按 id 解析。

    解析层不能一起过滤：模板库迁移到 `drivers/` 之前，老链路按 id 提交
    照常要能跑通（只是拿占位结果）。过滤放在解析层 = 把老链路打断。
    """
    router = _router(_spec("public-image"), _spec("fake-model", placeholder=True))

    listed = CatalogService(router).list_models(Capability.IMAGE)
    assert [item.id for item in listed.items] == ["public-image"]

    # 解析层不受影响
    assert router.resolve("fake-model") is not None
    assert router.provider_for("fake-model").name == "fake"
    # 巡检 / 维护仍看得到全部
    assert {spec.id for spec in router.models(Capability.IMAGE)} == {
        "public-image",
        "fake-model",
    }


def test_echo_placeholder_models_stay_out_of_public_catalog() -> None:
    """echo 占位供应商的模型不得出现在给人挑的目录里。

    真实声明：漏标一个，用户在生成工作台就能选中「Seedream 3」，
    提交后拿到 `https://example.com/mock/image.mp4` 的占位图——**全程
    不报错**，看起来像接了豆包，其实什么都没接。

    这批模型在 `drivers/echo.py`：整体迁移后它和其他驱动一样自动载入，
    不带 `_` 前缀是故意的（离线联调按 id 提交要能解析得到），
    只靠 `placeholder` 挡在选择器外。
    """
    from modules.model_driver.drivers.echo import EchoProvider

    public_ids = {spec.id for spec in get_model_router().public_models()}
    echo_ids = {spec.id for spec in EchoProvider.models}
    assert echo_ids, "echo 供应商声明的模型清单为空，用例失去意义"
    assert not public_ids & echo_ids, (
        f"echo 的占位模型漏进公开目录: {sorted(public_ids & echo_ids)}"
    )


def test_echo_models_are_all_marked_placeholder() -> None:
    """echo 声明的每一个模型都必须标 `placeholder=True`。

    兜底断言：不依赖上面那条「结果」守卫（那条只查当前交集），
    直接钉住「echo 的模型全部是占位」这条声明本身。
    """
    from modules.model_driver.drivers.echo import EchoProvider

    for spec in EchoProvider.models:
        assert spec.placeholder, f"echo: {spec.id} 未标记 placeholder"


def test_placeholder_models_do_not_shadow_real_models() -> None:
    """占位模型的 id 不得与真实供应商的模型重名。

    重名时 `ModelRouter.register` 会抛 `RegistryError`（好），但**顺序不同
    结果就不同**：占位供应商若先注册，真实模型后注册会直接炸掉整个服务
    启动；反之则占位模型被顶掉、但 echo 的那份清单还在，守卫会误判。

    更麻烦的是**将来**：模板库迁移到 `drivers/` 后，如果某个真实模型沿用
    了 `seedream-3` 这个 id，而 echo 还声明着同一个 id —— 那时候到底是
    「真实实现」还是「占位实现」在服务，取决于注册顺序。这条守卫把这种
    隐患提前钉住。
    """
    from infra import get_model_router
    from modules.model_driver.drivers.echo import EchoProvider

    router = get_model_router()
    echo_ids = {spec.id for spec in EchoProvider.models}
    real_ids = {
        spec.id
        for spec in router.models()
        if spec.provider != "echo" and not spec.placeholder
    }
    assert not echo_ids & real_ids, (
        f"占位模型与真实模型重名: {sorted(echo_ids & real_ids)}"
    )


def test_placeholder_and_internal_are_separate_concerns() -> None:
    """两个标记不能互相替代。

    `internal` 的语义是「露脸必然 400」（替换链的标识校验）；
    `placeholder` 的语义是「露脸不报错但结果是假的」。
    把替换链模型错标成 placeholder，它就会**露脸**给用户 —— 那是回归；
    把占位模型错标成 internal，用户会以为「这个模型没实现」，但实际
    它是「实现了但是假的」，排查方向会跑偏。
    """
    router = _router(
        _spec("chain-only", internal=True),
        _spec("fake-model", placeholder=True),
    )
    listed = CatalogService(router).list_models(Capability.IMAGE)
    assert listed.items == []

    # 两个都能解析
    assert router.resolve("chain-only") is not None
    assert router.resolve("fake-model") is not None


def test_placeholder_model_never_becomes_the_default() -> None:
    """占位模型**不能**当默认模型。

    这是个会直接打到线上的坑：占位模型不在公开目录里，而目录里的
    `is_default` 只跟公开模型比对。于是 —— 前端看到的是真实模型、目录里
    一个 `is_default` 都没有 → 前端「不传 model」→ 后端按默认取到占位模型
    → 跑出一张 `example.com/mock/` 的假图。**全程没有任何报错**。

    默认模型只能落在真实模型上。没有真实模型时，宁可**没有默认**：
    前端显式带 id，走 `resolve()` 照样拿得到占位实现（离线联调不受影响），
    但不会在用户毫不知情时替他选了假模型。
    """
    router = _router(_spec("fake-model", placeholder=True))

    assert router.default_model_or_none(Capability.IMAGE) is None

    # 有真实模型时，默认落在真实模型上，不是占位那个
    router2 = _router(
        _spec("fake-model", placeholder=True),
        _spec("real-image"),
    )
    assert router2.default_model_or_none(Capability.IMAGE) == "real-image"


def test_placeholder_models_do_not_block_public_default_marking() -> None:
    """占位模型占了默认位时，公开模型**仍然**要能被点亮为默认。

    这是上面那条的另一半：过滤掉占位模型不能顺手把默认标记也弄丢。
    默认标记要落在真实模型上（这里就是 `real-image`）。
    """
    router = _router(
        _spec("fake-model", placeholder=True),
        _spec("real-image"),
    )

    listed = CatalogService(router).list_models(Capability.IMAGE)
    assert [item.id for item in listed.items] == ["real-image"]
    assert listed.items[0].is_default is True


def test_preferred_for_ignores_placeholder_models() -> None:
    """供应商声明 `preferred_for` 时，占位模型不能成为首选。

    否则供应商可以把一个假模型声明成「该能力的默认首选」——那等于绕过了
    「占位不当默认」这条约束，从另一个门进来。
    """

    class _PreferredProvider(_FakeProvider):
        preferred_for = frozenset({Capability.IMAGE})

    router = ModelRouter()
    router.register(
        _PreferredProvider(
            "p",
            (
                _spec("fake-model", placeholder=True, provider="p"),
                _spec("real-image", provider="p"),
            ),
        )
    )

    assert router.default_model_or_none(Capability.IMAGE) == "real-image"
