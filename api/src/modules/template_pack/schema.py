"""封装的 HTTP 契约：草稿入参、管理视图、对外目录。

**为什么这些形状不放在 `modules/template`**：它们是素材包编辑器与选择器的
出参，跟着表一起搬到本模块（2026-11-14 C2）。跨模块共用的那一张卡片
（`ReplacementPack`）在 `core/template_pack_view.py`——替换模块要把它嵌进
`/replacement-catalog`，两边都不能 import 对方。

字段别名、默认值与校验时机与重建前一致：前端 41 个文件按这些形状对接。
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from core.schema import CamelModel
from core.template_pack_view import (
    PackBinding,
    PackKind,
    ReplacementPack,
    aligned_bindings,
    aligned_required_roles,
)

__all__ = [
    "PackAction",
    "PackBinding",
    "PackDraftIn",
    "PackManaged",
    "PackManagedList",
    "PackPublicList",
    "aligned_bindings",
    "aligned_required_roles",
]


class PackDraftIn(CamelModel):
    request_id: UUID
    expected_revision: int = Field(default=0, ge=0)
    kind: PackKind
    name: str = Field(default="", max_length=160)
    reference_library_ids: list[str] = Field(
        default_factory=list, max_length=10, description="封装用的参考图 id，按顺序"
    )
    cover_library_id: str | None = Field(
        default=None, description="封面的 asset id；必须出现在 referenceLibraryIds 里"
    )
    wardrobe_mode: Literal["preserve", "reference"] = "preserve"
    features: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=2000)
    bindings: list[PackBinding] = Field(default_factory=list, max_length=10)
    required_roles: list[str] = Field(default_factory=list, max_length=8)
    supported_scopes: list[str] = Field(default_factory=list, max_length=8)


class PackAction(CamelModel):
    expected_revision: int = Field(ge=0)


class PackManaged(ReplacementPack):
    revision: int = 0
    status: Literal["draft", "published", "disabled"] = "draft"
    # 服务端算好的「还差什么」，草稿列表和发布按钮直接用它，避免前后端各写一套规则
    gaps: list[str] = Field(default_factory=list)


class PackManagedList(CamelModel):
    items: list[PackManaged]


class PackPublicList(CamelModel):
    items: list[ReplacementPack]
    total: int
    page: int
    page_size: int
