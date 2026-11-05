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
    is_default: bool = False


class ModelListOut(CamelModel):
    items: list[ModelOut] = Field(default_factory=list)
    total: int = 0
