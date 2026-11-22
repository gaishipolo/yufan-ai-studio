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


class ReferenceLimitOut(CamelModel):
    """供应商对**一类**参考素材的硬性限制。

    与 `maxReferences` 的区别：那个是**总数**上限，这个是分类的。视频模型
    常见「5 张图 + 3 段音频 = 总数 8」—— 只把总数交给前端，用户加到第 6 张图
    时界面还说「还能再加」，提交才吃 400。前端按这里逐类算余量。

    `maxCount = 0` 表示这一类不限（供应商没声明）。
    """

    max_count: int = 0
    #: 图片**单边**像素范围，`0` 表示不限。音频不做边长校验。
    min_side: int = 0
    max_side: int = 0


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
    reference_kinds: list[str] = Field(
        default_factory=list,
        description=(
            "这个模型能收哪几类参考素材（`image` / `video` / `audio`），"
            "空数组 = 纯文生图。前端据此渲染参考素材的类型筛选与数量上限，"
            "不在界面里写死"
        ),
    )
    reference_limits: dict[str, ReferenceLimitOut] = Field(
        default_factory=dict,
        description=(
            "按素材类型分开的上限（键是 `image` / `audio` …）。"
            "`maxReferences` 仍是总数上限，两者要一起守；空对象 = 供应商没声明"
        ),
    )
    is_default: bool = False


class ModelListOut(CamelModel):
    items: list[ModelOut] = Field(default_factory=list)
    total: int = 0
