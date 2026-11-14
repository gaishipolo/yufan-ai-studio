"""素材包卡片的**跨模块共享形状**（core 层词汇，不带任何表）。

## 为什么放在 core

`ReplacementPack` 有两个生产者/消费者：封装模块用它描述「一张可选素材包卡片」，
替换模块把它嵌进 `/replacement-catalog` 的 `packs` 字段。两边分属不同模块，
`test_modules_do_not_import_each_other` 不许互相 import，各写一份又会在加字段时
悄悄漂移（表现是「接口里有、卡片上没有」，前端拿到 undefined）。

这与 `core/feed.py` 的 `ReplacementFeedRecord` 是同一种取舍：**一个模块产出、
另一个模块原样嵌入的载荷，中间那张形状归 core**。

## 为什么不放 ORM / 不放仓储

这里只有形状与对齐规则（用途补全、必填用途排序），没有一处查询、没有一处写入。
真正的读写在 `modules/template_pack`，执行链经 `core.ports.template_runtime` 取快照。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from core.schema import CamelModel
from core.template_rules.roles import BASE_ROLE, PACK_ROLES

__all__ = [
    "PackBinding",
    "PackKind",
    "ReplacementPack",
    "aligned_bindings",
    "aligned_required_roles",
]

#: 素材包（与替换任务）的对象分类。两套接口共用同一组取值。
PackKind = Literal["character", "product", "scene"]


# --------------------------------------------------------------------------- #
# 封装：逐图用途
# --------------------------------------------------------------------------- #
class PackBinding(CamelModel):
    """一张参考图在封装里的用途；description 只补充本次任务特有的事实。"""

    role: str = ""
    description: str = ""


def aligned_bindings(kind: str, reference_ids: list[str], bindings: list) -> list[PackBinding]:
    """把用途对齐到参考图列表：非法或缺失的用途落到基准用途，多余的截掉。

    旧包只有 reference_ids、没有用途，读出来必须是「每张都能用」的状态；
    前端提交的 bindings 长度不一致时也走这里兜底，不让脏数据进存储。
    """
    allowed = PACK_ROLES.get(kind, {})
    base = BASE_ROLE.get(kind, "front")
    rows = [b if isinstance(b, PackBinding) else PackBinding.model_validate(b) for b in bindings]
    roles = [row.role if row.role in allowed else base for row in rows]
    roles = (roles + [base] * len(reference_ids))[: len(reference_ids)]
    return [
        PackBinding(
            role=role,
            description=rows[index].description if index < len(rows) else "",
        )
        for index, role in enumerate(roles)
    ]


def aligned_required_roles(kind: str, required: list[str]) -> list[str]:
    """必填用途永远包含基准用途，顺序按用途表固定。"""
    allowed = PACK_ROLES.get(kind, {})
    base = BASE_ROLE.get(kind, "front")
    picked = [role for role in required if role in allowed and role != base]
    return [base, *picked]


# --------------------------------------------------------------------------- #
# 封装：对外形状
# --------------------------------------------------------------------------- #
class ReplacementPack(CamelModel):
    id: str
    kind: PackKind
    name: str
    reference_ids: list[str]
    cover_reference_id: str | None = None
    wardrobe_mode: Literal["preserve", "reference"] = "preserve"
    features: str = ""
    instructions: str = ""
    version: int = 0
    available: bool | None = None
    cover_url: str | None = None
    preview_urls: list[str] = Field(default_factory=list)
    reference_names: list[str] = Field(default_factory=list)
    # 逐图用途与 reference_ids 同序；缺省时前端按基准用途展示
    bindings: list[PackBinding] = Field(default_factory=list)
    required_roles: list[str] = Field(default_factory=list)
    supported_scopes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def align_pack_bindings(self) -> ReplacementPack:
        self.bindings = aligned_bindings(self.kind, self.reference_ids, self.bindings)
        self.required_roles = aligned_required_roles(self.kind, self.required_roles)
        return self
