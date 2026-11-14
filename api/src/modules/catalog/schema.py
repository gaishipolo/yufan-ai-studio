"""模型目录的出参定义。

契约与前端 `entities/model/model/types.ts` 一一对应。
`paramSchema` 直接透传供应商声明，前端据此渲染参数控件，
不认识的字段忽略即可——新增参数不需要改前端。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from core.schema import CamelModel
from infra.ai.base import Capability


class ModelOut(CamelModel):
    id: str
    label: str
    capability: Capability
    provider: str
    description: str = ""
    param_schema: dict[str, Any] = Field(default_factory=dict)
    max_references: int = Field(
        default=0,
        description=(
            "这个模型最多接受几张参考图（图生图 / 多图合成），`0` 表示纯文生图。"
            "参考图不是 `paramSchema` 里的参数：它的值是「一批图片」，"
            "提交时走 `referenceLibraryIds` 这条独立入参"
        ),
    )
    replacement: bool = Field(
        default=False,
        description=(
            "这个模型能不能跑替换链（按「原片 + 素材包」替换）。由驱动声明，"
            "模板目录按它筛；调用方不得从槽位组合推断"
        ),
    )
    is_default: bool = False


class ModelListOut(CamelModel):
    items: list[ModelOut] = Field(default_factory=list)
    total: int = 0
