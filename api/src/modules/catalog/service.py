"""模型目录服务：把 `ModelRouter` 的模型清单翻译成前端可直接用的结构。

这一层存在的意义是解除「前端硬编码模型 / 时长 / 比例」的耦合：
新增一个供应商模型，只是后端注册表多一条，前端自动出现在选择器里。
"""

from __future__ import annotations

from core.logging import get_logger
from infra.ai.base import (
    Capability,
    ModelReferenceLimitsCapable,
    ReferenceLimitsCapable,
)
from infra.ai.router import ModelRouter
from modules.catalog.schema import ModelListOut, ModelOut, ReferenceLimitOut

logger = get_logger(__name__)


class CatalogService:
    def __init__(self, router: ModelRouter) -> None:
        self.router = router

    def _reference_limits(self, model_id: str) -> dict[str, ReferenceLimitOut]:
        """供应商声明的**按类**上限（图片 5 张、音频 3 段…）。

        只认 `ReferenceLimitsCapable` 这个协议，不用 `getattr` 探测：声明与不声明
        是两种能力，靠属性名猜会「猜对了但意思是别的」。

        解析失败返回空：目录是只读展示，一个供应商装配异常不该让整页 500；
        前端在空对象时退回「只守总数」的老行为。
        """
        try:
            provider = self.router.provider_for(model_id)
        except Exception:  # noqa: BLE001 - 目录接口不该因为装配异常整体失败
            logger.warning("解析供应商失败，参考上限按未声明处理 model=%s", model_id)
            return {}
        if isinstance(provider, ModelReferenceLimitsCapable):
            raw = provider.reference_limits_for(model_id)
        elif isinstance(provider, ReferenceLimitsCapable):
            raw = provider.reference_limits
        else:
            return {}
        return {
            kind: ReferenceLimitOut(
                max_count=limit.max_count,
                min_side=limit.min_side,
                max_side=limit.max_side,
            )
            for kind, limit in raw.items()
        }

    def list_models(self, capability: Capability | None = None) -> ModelListOut:
        """给人挑的模型清单：过滤 `internal=True` / `placeholder=True` 的模型。

        `is_default` 只在公开模型里点亮。默认模型落在被过滤掉的模型上时，
        这里**不会**把默认标记挪给别的模型——那样前端会「认为默认是 A、
        于是不传 model」，后端却按默认取到 B，直接 400。宁可一个默认都没有，
        让前端显式带上 id（`ModelRouter` 在注册时也已跳过 internal / placeholder）。
        """
        items = [
            ModelOut(
                id=spec.id,
                label=spec.label,
                capability=spec.capability,
                provider=spec.provider,
                description=spec.description,
                param_schema=dict(spec.param_schema),
                max_references=spec.max_references,
                replacement=spec.replacement,
                reference_kinds=list(spec.reference_kinds),
                reference_limits=self._reference_limits(spec.id),
                aliases=list(spec.aliases),
                is_default=spec.id == self.router.default_model_or_none(spec.capability),
            )
            for spec in self.router.public_models(capability)
        ]
        return ModelListOut(items=items, total=len(items))
