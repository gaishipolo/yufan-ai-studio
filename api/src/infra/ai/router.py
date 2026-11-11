"""模型路由：`model` 字符串 → 具体供应商。

业务层只说「用 seedance-2.5 生成」，不需要知道它属于哪个厂商；
换厂商或做灰度时只改这里注册的模型归属，业务代码零改动。

同时承担两个职责：
1. 提供模型目录（前端「模型选择器」的数据源，含参数 schema）
2. 解析某个模型由哪个 provider 承接
"""

from __future__ import annotations

from typing import TypedDict

from core.exceptions import BadRequest
from core.logging import get_logger
from core.registry import NamedRegistry, RegistryError
from infra.ai.base import Capability, ModelProvider, ModelSpec

logger = get_logger(__name__)


class ModelSummaryItem(TypedDict):
    id: str
    label: str
    capability: str
    provider: str


class RouterSummary(TypedDict):
    providers: list[str]
    models: list[ModelSummaryItem]


class ModelRouter:
    def __init__(self) -> None:
        self._providers = NamedRegistry[ModelProvider]("ai.provider")
        self._models: dict[str, ModelSpec] = {}
        # 别名：前端历史上按 label（中文名）传 model，需能解析回 id
        self._alias: dict[str, str] = {}
        self._default: dict[Capability, str] = {}

    # ------------------------------------------------------------------ 注册
    def register(self, provider: ModelProvider) -> None:
        """注册供应商。**先全量校验，再落库** —— 中途抛错会留下残局。

        旧写法是「先 `add` 供应商、边循环边校验模型 id」：撞车时抛错，
        但驱动已经进了注册表、前几个模型也已经进了 `_models` / `_default`。
        而 `ensure_drivers_registered` 会把它标成 `loaded=False` 且不再重试，
        于是清单显示「加载失败」，它那几个模型却仍能被 `resolve()` 选中并提交。
        """
        for spec in provider.models:
            if spec.provider != provider.name:
                raise RegistryError(
                    f"模型 {spec.id} 声明归属 {spec.provider}，但由 {provider.name} 注册"
                )
            existing = self._models.get(spec.id)
            if existing is not None and existing.provider != provider.name:
                raise RegistryError(
                    f"模型 {spec.id} 被多个供应商声明: "
                    f"{existing.provider} / {provider.name}"
                )

        # 供应商重名也提前判：`NamedRegistry.add` 同样会抛，但那时代码已经
        # 走过了上面的校验、下面的写入顺序就没人保证了
        if provider.name in self._providers.names():
            raise RegistryError(f"供应商 {provider.name!r} 已注册")

        self._providers.add(provider.name, provider)

        for spec in provider.models:
            self._models[spec.id] = spec
            self._register_alias(spec)
            self._default.setdefault(spec.capability, spec.id)

        self._apply_preferred(provider)

    def _apply_preferred(self, provider: ModelProvider) -> None:
        """应用供应商声明的「该能力的首选默认模型」（可选字段 `preferred_for`）。

        默认模型原本完全由注册顺序决定（`setdefault` 先到先得），而顺序是装配
        细节：`AI_PROVIDERS` 里的供应商先注册、`drivers/` 里的驱动后注册。于是
        把 agnes 从 `AI_PROVIDERS` 挪进 `drivers/`，默认生图模型就会**静默**
        变成 echo 的 seedream-3——没有一处报错，用户只看到出的是占位图。

        所以「谁是默认」必须由供应商自己声明，而不是靠谁先被 import。
        """
        preferred = getattr(provider, "preferred_for", ()) or ()
        for capability in preferred:
            candidates = [
                spec.id for spec in provider.models if spec.capability is capability
            ]
            if not candidates:
                logger.warning(
                    "供应商 %s 声明 %s 为首选，但它没有该能力的模型，已忽略",
                    provider.name,
                    getattr(capability, "value", capability),
                )
                continue
            self._default[capability] = candidates[0]

    def register_specs(self, specs: tuple[ModelSpec, ...], provider_name: str) -> None:
        """补充单个模型的元信息（供应商没在 `models` 里全量列出时使用）。"""
        for spec in specs:
            if spec.provider != provider_name:
                raise RegistryError(f"模型 {spec.id} 归属与注册名不一致")
            self._models.setdefault(spec.id, spec)
            self._register_alias(spec)
            self._default.setdefault(spec.capability, spec.id)

    def _register_alias(self, spec: ModelSpec) -> None:
        """登记 label → id 的别名，冲突时**不能静默**。

        两个供应商都叫「Seedance 2.5」时，先注册的会永远赢，后一个模型
        在日志里没有任何痕迹——排查「为什么请求打到了另一家」会非常痛苦。
        """
        existing = self._alias.get(spec.label)
        if existing is not None and existing != spec.id:
            logger.warning(
                "模型别名冲突 label=%r 已指向 %s，本次注册的 %s 不会被按名字解析；"
                "请改用 id 区分",
                spec.label,
                existing,
                spec.id,
            )
            return
        self._alias[spec.label] = spec.id

    # ------------------------------------------------------------------ 解析
    def resolve(self, model: str) -> ModelSpec | None:
        """按 id 或 label 解析模型。"""
        spec = self._models.get(model)
        if spec is not None:
            return spec
        real_id = self._alias.get(model)
        return self._models.get(real_id) if real_id else None

    def provider_for(self, model: str, *, allow_fallback: bool = True) -> ModelProvider:
        """按模型找供应商。

        `allow_fallback=False` 关掉「只有一个供应商就兜底」：那条兜底是给本地
        echo 联调用的（模型名随便写也能跑）。**落过库的记录不能沾**——原渠道的
        模型被下线或改名之后，重试会把这条任务悄悄打到另一家供应商上，产物风格、
        单价、合规口径全变，日志里还看不出异常。
        """
        spec = self.resolve(model)
        if spec is not None:
            return self._providers.get(spec.provider)

        # 未声明的模型名：只有一个供应商时兜底（本地 echo 联调场景）
        if allow_fallback and len(self._providers) == 1:
            return self._providers.require()
        if not allow_fallback:
            # 走严格模式的都是「按历史记录找原渠道」，说法要指向真正的原因，
            # 否则用户只会看到「未知模型」而不知道是自己停用了某个渠道
            raise BadRequest(
                f"模型 {model!r} 不在当前模型目录中（原渠道可能已停用），"
                f"可用模型: {sorted(self._models)}"
            )
        raise BadRequest(f"未知模型 {model!r}，可用模型: {sorted(self._models)}")

    def spec_for(self, model: str) -> ModelSpec | None:
        return self.resolve(model)

    def default_model(self, capability: Capability) -> str:
        model = self._default.get(capability)
        if model is None:
            raise BadRequest(f"尚未注册任何 {capability.value} 模型")
        return model

    def default_model_or_none(self, capability: Capability) -> str | None:
        """给「模型目录」接口用：没有默认值不算错误。"""
        return self._default.get(capability)

    # ------------------------------------------------------------------ 目录
    def models(self, capability: Capability | None = None) -> tuple[ModelSpec, ...]:
        specs = self._models.values()
        if capability is not None:
            specs = [s for s in specs if s.capability == capability]  # type: ignore[assignment]
        return tuple(sorted(specs, key=lambda s: (s.capability, s.id)))

    def provider_names(self) -> list[str]:
        return self._providers.names()

    def summary(self) -> RouterSummary:
        return {
            "providers": self.provider_names(),
            "models": [
                {
                    "id": spec.id,
                    "label": spec.label,
                    "capability": spec.capability.value,
                    "provider": spec.provider,
                }
                for spec in self.models()
            ],
        }

    def clear(self) -> None:
        """仅测试使用。"""
        self._providers.clear()
        self._models.clear()
        self._alias.clear()
        self._default.clear()
