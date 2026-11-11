"""模型目录服务：把 `ModelRouter` 的模型清单翻译成前端可直接用的结构。

这一层存在的意义是解除「前端硬编码模型 / 时长 / 比例」的耦合：
新增一个供应商模型，只是后端注册表多一条，前端自动出现在选择器里。
"""

from __future__ import annotations

from infra.ai.base import Capability
from infra.ai.router import ModelRouter
from modules.catalog.schema import ModelListOut, ModelOut


class CatalogService:
    def __init__(self, router: ModelRouter) -> None:
        self.router = router

    def list_models(self, capability: Capability | None = None) -> ModelListOut:
        """给人挑的模型清单：过滤 `internal=True`（替换链专用渠道）。

        `is_default` 只在公开模型里点亮。内部模型若恰好是该能力的默认值，
        这里**不会**把默认标记挪给别的模型——那样前端会「认为默认是 A、
        于是不传 model」，后端却按默认取到内部模型 B，直接 400。宁可一个
        默认都没有，让前端显式带上 id。
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
                is_default=spec.id == self.router.default_model_or_none(spec.capability),
            )
            for spec in self.router.public_models(capability)
        ]
        return ModelListOut(items=items, total=len(items))
